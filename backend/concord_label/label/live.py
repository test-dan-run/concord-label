"""Live evaluation while labelling (PLAN §6.4): model scores against the segments confirmed so far.

- References are confirmed segments with text, in the order they were confirmed.
- A model's hypothesis for a segment is its cached output on exactly that span, else the member output
  pseudo-labelling stored for it. Nothing is inferred here, so the page is cheap to refresh.
- Below MIN_RANK confirmed segments the standings say "too few to rank". 95% intervals are a
  percentile bootstrap over segments (corpus WER of each resample).
- DER uses only fully reviewed files (every segment reviewed), so missing labels never count as misses.
- The locked benchmark score comes from Concord Bench (SPLIT_PLAN.md §5.3, CONTRACTS.md §4): one request
  per page load, for the newest version Bench holds of this dataset, matched to models by registry name.
  Without a reachable Bench (or with no package of this dataset there) it is None and the page hides it.
"""

from __future__ import annotations

from typing import Any

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import adapters, peer
from ..core.models import AudioFile, Dataset, Model, ModelOutput, Segment, SegmentStatus
from ..cuts import usable_files
from ..metrics import der, normalisers
from ..metrics.der import Turn
from ..runs import cache
from . import fusion

MIN_RANK = 30
BOOTSTRAP = 1000
CURVE_POINTS = 60


def bootstrap_ci(errors: np.ndarray, words: np.ndarray, n: int = BOOTSTRAP, seed: int = 0) -> tuple[float, float] | None:
    if len(errors) < 2 or words.sum() == 0:
        return None
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(errors), size=(n, len(errors)))
    w = words[idx].sum(axis=1)
    keep = w > 0
    vals = errors[idx].sum(axis=1)[keep] / w[keep]
    lo, hi = np.percentile(vals, [2.5, 97.5])
    return float(lo), float(hi)


def _confirmed(session: Session, files: list[AudioFile]) -> list[Segment]:
    segs = [s for f in files for s in f.segments if s.status == SegmentStatus.confirmed and s.text is not None]
    return sorted(segs, key=lambda s: (s.edited_at or s.updated_at, s.id))


def _outputs(session: Session, service: str, phash: str, shas: list[str]) -> dict[tuple[str, float, float], ModelOutput]:
    rows = session.scalars(select(ModelOutput).where(ModelOutput.model_name == service, ModelOutput.params_hash == phash,
                                                     ModelOutput.audio_sha256.in_(shas)))
    return {(r.audio_sha256, r.span_start, r.span_end): r for r in rows}


def _locked(ds: Dataset, nkey: str) -> dict[str, dict[str, Any]]:
    """Each model's locked benchmark score in Concord Bench, by registry name ({} without Bench)."""
    from .sync import install_id

    return peer.locked_scores(ds.uid, install_id(), nkey) or {}


def asr_standings(session: Session, ds: Dataset, files: list[AudioFile], normaliser: str | None) -> dict[str, Any]:
    norm = normalisers.get(normaliser or normalisers.DEFAULT)
    segs = _confirmed(session, files)
    sha = {f.id: f.sha256 for f in files}
    dur = {f.id: f.duration or 0.0 for f in files}
    refs = norm.many([s.text or "" for s in segs])
    models = []
    member_ids = [i for s in segs for i in ((s.meta or {}).get("members") or {}).values()]
    member_rows = cache.outputs_for(session, member_ids)
    locked = _locked(ds, norm.key)
    for m in session.scalars(select(Model).order_by(Model.display_name)):
        if not m.is_asr:
            continue
        try:
            a = adapters.for_model(m, ds.language)
        except adapters.AdapterError:
            continue
        have = _outputs(session, a.service, a.params_hash, list(set(sha.values())))
        hyps: list[str | None] = []
        for s in segs:
            whole = s.start <= 0.0 and s.end >= dur[s.audio_file_id] - 1e-3
            row = have.get((sha[s.audio_file_id], 0.0, cache.WHOLE) if whole else
                           (sha[s.audio_file_id], round(s.start, 3), round(s.end, 3)))
            if row is None:
                mid = ((s.meta or {}).get("members") or {}).get(a.service)
                row = member_rows.get(mid) if mid else None
            hyps.append(row.output.get("text", "") if row else None)
        covered = [k for k, h in enumerate(hyps) if h is not None]
        if not covered:
            continue
        hn = norm.many([hyps[k] or "" for k in covered])
        err = np.zeros(len(segs), dtype=float)
        words = np.zeros(len(segs), dtype=float)
        for k, h in zip(covered, hn, strict=True):
            err[k], words[k] = fusion.errors(refs[k], h)
        has = np.zeros(len(segs), dtype=bool)
        has[covered] = True
        cum_e, cum_n = np.cumsum(err), np.cumsum(words)
        step = max(1, len(segs) // CURVE_POINTS)
        curve = [{"confirmed": k + 1, "wer": float(cum_e[k] / cum_n[k]) if cum_n[k] else None}
                 for k in range(len(segs)) if (k + 1) % step == 0 or k == len(segs) - 1]
        tot_n = words[has].sum()
        models.append({"model_id": m.id, "name": m.display_name, "service": m.registry_name,
                       "coverage": len(covered), "words": int(tot_n),
                       "wer": float(err[has].sum() / tot_n) if tot_n else None,
                       "ci": bootstrap_ci(err[has], words[has]) if len(segs) >= MIN_RANK else None,
                       "curve": curve, "locked": locked.get(m.registry_name)})
    ranked = len(segs) >= MIN_RANK
    models.sort(key=lambda r: (r["wer"] is None, r["wer"] if r["wer"] is not None else 0))
    for k, r in enumerate(models):
        r["rank"] = k + 1 if ranked and r["wer"] is not None else None
    return {"normaliser": norm.key, "confirmed": len(segs), "min_rank": MIN_RANK, "ranked": ranked, "models": models}


def der_card(session: Session, files: list[AudioFile], collar: float) -> dict[str, Any]:
    complete = [f for f in files if f.segments and all(s.reviewed for s in f.segments)
                and any(s.speaker for s in f.segments if s.status == SegmentStatus.confirmed)]
    out: dict[str, Any] = {"files": len(complete), "of": len(files), "collar": collar, "models": []}
    if not complete:
        return out
    shas = [f.sha256 for f in complete]
    for m in session.scalars(select(Model).order_by(Model.display_name)):
        if not m.has_speakers:
            continue
        try:
            a = adapters.for_model(m)
        except adapters.AdapterError:
            continue
        have = _outputs(session, a.service, a.params_hash, shas)
        rows = []
        for f in complete:
            row = have.get((f.sha256, 0.0, cache.WHOLE))
            if row is None:
                continue
            ref = [Turn(s.start, s.end, s.speaker) for s in f.segments if s.status == SegmentStatus.confirmed and s.speaker]
            hyp = [Turn(s, e, k) for (s, e), k in zip(row.output["segments"], row.output.get("speakers") or [],
                                                      strict=False)]
            d = der.der(ref, hyp, f.duration or 0.0, collar)
            rows.append({k: v for k, v in d.items() if k != "mapping"})
        if rows:
            agg = der.aggregate(rows)
            out["models"].append({"model_id": m.id, "name": m.display_name, "der": agg["der"], "jer": agg["jer"],
                                  "files": agg["files"]})
    out["models"].sort(key=lambda r: (r["der"] is None, r["der"] or 0))
    return out


def feed(files: list[AudioFile], limit: int = 20) -> list[dict[str, Any]]:
    stems = {f.id: f.stem for f in files}
    segs = [s for f in files for s in f.segments if s.status == SegmentStatus.confirmed and s.edited_at is not None]
    segs.sort(key=lambda s: s.edited_at, reverse=True)
    return [{"id": s.id, "file_id": s.audio_file_id, "file": stems[s.audio_file_id], "start": s.start, "end": s.end,
             "speaker": s.speaker, "text": s.text, "confirmed_at": s.edited_at.isoformat()} for s in segs[:limit]]


def live(session: Session, ds: Dataset, normaliser: str | None = None, collar: float | None = None) -> dict[str, Any]:
    files = usable_files(session, ds.id)
    collar = der.DEFAULT_COLLAR if collar is None else collar
    return {"dataset": {"id": ds.id, "name": ds.name}, "asr": asr_standings(session, ds, files, normaliser),
            "der": der_card(session, files, collar), "kws": {"available": False}, "feed": feed(files)}
