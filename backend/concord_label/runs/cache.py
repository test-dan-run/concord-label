"""The inference cache (`model_output`) and the Model Catalog's output index.

Rows are keyed on (model, audio sha256, span, params hash, build key). The params are the adapter's own
(adapters/__init__.py) and never hold the image or build; the build key is the catalog's, so a rebuilt image
or a changed model config means new outputs, and old rows (no build key) never match.

`OutputCache` answers for one model and build, in batches (a file, or a whole stage):

1. Label's own rows;
2. the catalog's output index (`index_lookup`) for the rest. A hit another app owns (Concord Bench) is read
   from that app (`POST {owner_url}/api/outputs/batch`) and copied here with its provenance
   (`source_app`, `source_id`), because segments, fusion and live evaluation refer to local row ids;
3. otherwise the model runs, through the catalog's gateway.

Rows made or copied here are recorded in the index (`index_record`) once committed (`record_index`), so the
other app finds them too. A model whose build key is not known yet (the catalog is still hashing its
weights) still runs, but its rows are never indexed and never matched. A catalog or owner that does not
answer only costs reuse: the work is computed here.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

import httpx
import numpy as np
from aicore.catalog import CatalogError, index_key
from aicore.catalog import span as index_span
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..adapters.base import Adapter, AudioRef
from ..core.models import ModelOutput
from ..platform import catalog

WHOLE = -1.0
Key = tuple[str, float, float]  # (audio sha256, span start, span end); the whole file is (sha, 0.0, WHOLE)
CHUNK = 500


def span(start: float | None, end: float | None) -> tuple[float, float]:
    """A span as rows store it: rounded to 3 decimals, the whole file as (0.0, WHOLE). The index stores the
    whole file as (-1, -1) (`aicore.catalog.span`)."""
    if start is None or end is None or end < 0:
        return 0.0, WHOLE
    return round(float(start), 3), round(float(end), 3)


def key(sha256: str, start: float | None = 0.0, end: float | None = WHOLE) -> Key:
    return (sha256, *span(start, end))


def lookup(session: Session, service: str, build_key: str | None, sha256: str, start: float, end: float,
           phash: str) -> ModelOutput | None:
    if not build_key:
        return None
    s, e = span(start, end)
    return session.scalar(select(ModelOutput).where(
        ModelOutput.model_name == service, ModelOutput.build_key == build_key, ModelOutput.params_hash == phash,
        ModelOutput.audio_sha256 == sha256, ModelOutput.span_start == s, ModelOutput.span_end == e))


def local_rows(session: Session, service: str, build_key: str | None, phash: str,
               shas: Iterable[str]) -> dict[Key, ModelOutput]:
    """Every row of this model, build and params on these files, by key. None without a build key."""
    if not build_key:
        return {}
    shas = sorted(set(shas))
    out: dict[Key, ModelOutput] = {}
    for i in range(0, len(shas), CHUNK):
        for r in session.scalars(select(ModelOutput).where(
                ModelOutput.model_name == service, ModelOutput.build_key == build_key,
                ModelOutput.params_hash == phash, ModelOutput.audio_sha256.in_(shas[i:i + CHUNK]))):
            out[(r.audio_sha256, r.span_start, r.span_end)] = r
    return out


def read_outputs(owner_url: str, ids: list[int]) -> dict[int, dict[str, Any]]:
    """Outputs another app holds, by its ids (its `POST /api/outputs/batch`); missing ids are left out."""
    out: dict[int, dict[str, Any]] = {}
    for i in range(0, len(ids), 200):
        r = httpx.post(f"{owner_url.rstrip('/')}/api/outputs/batch", json={"ids": ids[i:i + 200]}, timeout=60.0)
        r.raise_for_status()
        for o in r.json().get("outputs") or []:
            out[int(o["id"])] = o
    return out


def served(row: ModelOutput) -> dict[str, Any]:
    """A row as `POST /api/outputs/batch` serves it (platform/catalog/README.md "What every app serves")."""
    s, e = index_span(row.span_start, row.span_end)
    return {"id": row.id, "model": row.model_name, "build_key": row.build_key, "audio_sha256": row.audio_sha256,
            "span_start": s, "span_end": e, "params_hash": row.params_hash, "params": row.params,
            "output": row.output, "latency_s": row.latency_s}


class OutputCache:
    """Outputs of one model (adapter) and build: resolve keys in batches, run what is missing, and record new
    rows in the catalog's index after the caller commits (`record_index`)."""

    def __init__(self, session: Session, adapter: Adapter, build_key: str | None, image: str | None = None,
                 log: Callable[[str], None] | None = None):
        self.session = session
        self.adapter = adapter
        self.build_key = build_key
        self.image = image
        self.log = log or (lambda _m: None)
        self.pending: list[ModelOutput] = []
        self.copied = 0
        self._said: set[str] = set()

    @property
    def service(self) -> str:
        return self.adapter.service

    @property
    def phash(self) -> str:
        return self.adapter.params_hash

    def _once(self, what: str, msg: str) -> None:
        if what not in self._said:
            self._said.add(what)
            self.log(msg)

    def _index_key(self, k: Key) -> dict[str, Any]:
        assert self.build_key
        return index_key(self.build_key, k[0], k[1], k[2], self.phash)

    # ------------------------------------------------------------ lookups

    def local(self, keys: Iterable[Key]) -> dict[Key, ModelOutput]:
        keys = list(keys)
        have = local_rows(self.session, self.service, self.build_key, self.phash, {k[0] for k in keys})
        return {k: have[k] for k in keys if k in have}

    def hits(self, keys: Iterable[Key]) -> dict[Key, dict[str, Any]]:
        """Index hits another app owns for these keys (one batched lookup); {} without a build key or catalog."""
        keys = list(dict.fromkeys(keys))
        if not keys or not self.build_key:
            return {}
        try:
            found = catalog.get_catalog().index_lookup(catalog.APP, [self._index_key(k) for k in keys])
        except (CatalogError, httpx.HTTPError) as exc:
            self._once("lookup", f"{self.service}: output index not reachable ({exc}); computing here")
            return {}
        return {k: h for k, h in zip(keys, found, strict=False)
                if h and h.get("owner") != catalog.APP and h.get("owner_url")}

    def _read(self, hits: dict[Key, dict[str, Any]]) -> dict[Key, dict[str, Any]]:
        """The outputs behind other apps' hits, checked against the keys (build, params, audio, span)."""
        by_owner: dict[str, list[tuple[Key, dict[str, Any]]]] = {}
        for k, h in hits.items():
            by_owner.setdefault(h["owner_url"], []).append((k, h))
        out: dict[Key, dict[str, Any]] = {}
        for url, items in by_owner.items():
            try:
                got = read_outputs(url, sorted({int(h["output_id"]) for _, h in items}))
            except (httpx.HTTPError, ValueError, KeyError) as exc:
                self._once(f"read {url}", f"{self.service}: could not read outputs from {url} ({exc}); computing here")
                continue
            for k, h in items:
                o = got.get(int(h["output_id"]))
                if o is None:
                    continue
                if (o.get("build_key") != self.build_key or o.get("params_hash") != self.phash
                        or o.get("audio_sha256") != k[0]
                        or index_span(o.get("span_start"), o.get("span_end")) != index_span(k[1], k[2])
                        or not isinstance(o.get("output"), dict)):
                    continue
                out[k] = {**o, "owner": h["owner"]}
        return out

    def peek(self, keys: Iterable[Key]) -> dict[Key, dict[str, Any]]:
        """Outputs other apps hold for these keys, read but not stored (previews)."""
        return self._read(self.hits(keys))

    def copy(self, hits: dict[Key, dict[str, Any]]) -> dict[Key, ModelOutput]:
        """Read other apps' outputs for these hits and store them here, keeping where they came from."""
        out: dict[Key, ModelOutput] = {}
        for k, o in self._read(hits).items():
            row = ModelOutput(model_name=self.service, audio_sha256=k[0], span_start=k[1], span_end=k[2],
                              params_hash=self.phash, build_key=self.build_key, params=o.get("params"),
                              output=o["output"], latency_s=float(o.get("latency_s") or 0.0), image=self.image,
                              source_app=o["owner"], source_id=int(o["id"]))
            out[k] = self._store(row, k)
        if out:
            self.copied += len(out)
            owners = sorted({r.source_app for r in out.values() if r.source_app})
            self.log(f"{self.service}: {len(out)} output(s) copied from {', '.join(owners) or 'another app'}")
        return out

    def resolve(self, keys: Iterable[Key]) -> dict[Key, ModelOutput]:
        """Rows for these keys: Label's own, else copies of what another app holds. Missing keys are left out."""
        keys = list(dict.fromkeys(keys))
        found = self.local(keys)
        missing = [k for k in keys if k not in found]
        if missing:
            found.update(self.copy(self.hits(missing)))
        return found

    # ------------------------------------------------------------ inference

    def _store(self, row: ModelOutput, k: Key) -> ModelOutput:
        try:
            with self.session.begin_nested():  # savepoint: losing a race must not undo the caller's work
                self.session.add(row)
        except IntegrityError:  # another process stored it first
            other = lookup(self.session, self.service, self.build_key, k[0], k[1], k[2], self.phash)
            assert other is not None
            return other
        if self.build_key:
            self.pending.append(row)
        return row

    def run(self, audio: np.ndarray, sha256: str, start: float = 0.0, end: float = WHOLE) -> ModelOutput:
        """Run the model (through the catalog's gateway) and store the output. `audio` is the exact samples."""
        k = key(sha256, start, end)
        if not self.build_key:
            self._once("no build", f"{self.service}: the catalog has no build key for it yet (weights still being "
                                   "hashed?): its outputs are kept but cannot be reused or shared")
        output, latency = self.adapter.run(audio, AudioRef(sha256, k[1], k[2]))
        row = ModelOutput(model_name=self.service, audio_sha256=sha256, span_start=k[1], span_end=k[2],
                          params_hash=self.phash, build_key=self.build_key, params=self.adapter.params, output=output,
                          latency_s=latency, image=self.image)
        return self._store(row, k)

    def get_or_run(self, audio: np.ndarray, sha256: str, start: float = 0.0,
                   end: float = WHOLE) -> tuple[ModelOutput, bool]:
        """(row, hit?) for one call. Batch with `resolve` first when there are many."""
        k = key(sha256, start, end)
        row = self.resolve([k]).get(k)
        if row is not None:
            return row, True
        return self.run(audio, sha256, start, end), False

    # ------------------------------------------------------------ the index

    def record_index(self) -> int:
        """Record the rows made or copied since the last call in the catalog's index. Call after commit."""
        rows = [r for r in self.pending if r.id is not None]
        self.pending = []
        if not rows or not self.build_key:
            return 0
        entries = [{**self._index_key((r.audio_sha256, r.span_start, r.span_end)), "output_id": r.id} for r in rows]
        try:
            return catalog.get_catalog().index_record(catalog.APP, entries)
        except (CatalogError, httpx.HTTPError) as exc:
            self._once("record", f"{self.service}: could not record outputs in the catalog's index ({exc})")
            return 0


def outputs_for(session: Session, ids: list[int]) -> dict[int, ModelOutput]:
    ids = [i for i in ids if i is not None]
    if not ids:
        return {}
    return {r.id: r for r in session.scalars(select(ModelOutput).where(ModelOutput.id.in_(ids)))}


def as_dict(row: ModelOutput) -> dict[str, Any]:
    return {"id": row.id, "model": row.model_name, "span": [row.span_start, row.span_end], "output": row.output,
            "latency_s": row.latency_s, "build_key": row.build_key, "source_app": row.source_app}
