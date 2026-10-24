"""The Model Catalog, as Concord Label uses it (platform/catalog/README.md is the contract).

Label does not register, verify, start or call model services itself. The catalog does, and Label:

- **lists models** from it (`models()`, cached for a few seconds) and keeps one `model` row per catalog model
  it has used (`ensure_model`), because runs and outputs refer to model ids. Each read of a record refreshes
  the row's capabilities, timing level, protocol and build key, so the row works as a cache while the
  catalog is down;
- **takes leases** for its jobs (`leased`): one lease per pipeline stage, through aicore's client, which
  heartbeats and waits in the catalog's queue. A lease that fails, or a catalog that does not answer, blocks
  the job (`ServicesBlocked`);
- **sends inference through the gateway**: with AICORE_CATALOG set (core/settings.py), `aicore.client(name)`
  points at `<catalog>/m/<name>`;
- **uses the output index** from runs/cache.py, and registers where other apps read its outputs
  (`register_app`, at API startup).

In replay mode (REPLAY_DIR) the catalog is an in-memory `FakeCatalog` built from the recordings
(`replay_catalog`), so tests, demos and the e2e server need no running catalog. Tests swap in their own
with `set_catalog`.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path
from typing import Any

import httpx
from aicore.catalog import Catalog, CatalogError, FakeCatalog, LeaseFailed
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.models import Model, utcnow
from ..core.settings import get_settings

log = logging.getLogger("concord_label.catalog")

APP = "label"  # this app's name in the catalog (leases `label:job-<id>`, the output index, POST /api/apps)
RECORDS_TTL_S = 5.0
TASKS = ("asr", "vad", "diar", "kws")
# How the Models page groups the catalog's models, by the tasks its verifier found (Label runs the first
# two; speech enhancement is listed so the catalog's models are all in sight).
GROUPS = {"asr": ("asr",), "seg": ("vad", "diar"), "enhance": ("enhance",)}
TASK_NAMES = {"enhance": "speech enhancement", "itn": "inverse text normalisation"}
USABLE = ("asr", "chat-audio", "segmenter")

Log = Callable[[str], None]
OnWait = Callable[[dict[str, Any]], None]


class ServicesBlocked(Exception):
    """The catalog could not give a job its models: the lease failed, or the catalog did not answer."""


class CatalogUnavailable(Exception):
    """The catalog did not answer (or refused) a read."""


# ---------------------------------------------------------------- which catalog

_override: Any = None


def set_catalog(cat: Any) -> None:
    """Use this catalog (tests: a FakeCatalog) instead of the configured one; None goes back to the setting."""
    global _override
    _override = cat
    clear_cache()


@lru_cache(maxsize=4)
def _http_catalog(url: str) -> Catalog:
    return Catalog(url, timeout=30.0)


def get_catalog() -> Any:
    """The configured catalog: a test's stand-in, the replay catalog in replay mode, else the real one."""
    if _override is not None:
        return _override
    s = get_settings()
    if s.replay_dir is not None:
        return replay_catalog(str(s.replay_dir), str(s.aicore_endpoints or ""))
    return _http_catalog(s.catalog_root)


# ---------------------------------------------------------------- replay mode

def usable_as(rec: dict[str, Any]) -> str | None:
    """The probe that applies (README "usable_as"), from the capability record or else from the protocol."""
    caps = rec.get("capabilities") or {}
    if caps.get("usable_as") is not None or "usable_as" in caps:
        return caps.get("usable_as")
    contract, api = rec.get("contract"), rec.get("api")
    if contract == "openai" and api == "audio.transcriptions":
        return "asr"
    if contract == "openai" and api == "chat.completions":
        return "chat-audio"
    if contract == "oip-v2" and api == "infer":
        inputs = {t.get("name") for t in (rec.get("signature") or {}).get("inputs", [])}
        if {"AUDIO", "SAMPLE_RATE"} <= inputs:
            return "segmenter"
    return None


def replay_build_key(name: str, image: str | None) -> str:
    return hashlib.sha256(f"replay|{name}|{image}".encode()).hexdigest()[:16]


@lru_cache(maxsize=4)
def replay_catalog(replay_dir: str, endpoints: str = "") -> FakeCatalog:
    """A FakeCatalog standing in for the catalog in replay mode: every service of the recorded endpoint map,
    with the capabilities recorded for the fixture models (`summary.json` "checks"), all `running`. The
    fixture models and text.transform services are registered. Build keys are made up but stable."""
    import yaml

    root = Path(replay_dir)
    ep_path = Path(endpoints) if endpoints else root / "endpoints.generated.yaml"
    services = (yaml.safe_load(ep_path.read_text()) or {}).get("services", {}) if ep_path.is_file() else {}
    summary = root / "summary.json"
    checks = (json.loads(summary.read_text()).get("checks") or {}) if summary.is_file() else {}
    aliases: dict[str, list[str]] = {}
    for key, ep in services.items():
        if key != ep["name"]:
            aliases.setdefault(ep["name"], []).append(key)
    records = []
    for key, ep in services.items():
        name = ep["name"]
        if key != name:
            continue
        build_key = replay_build_key(name, ep.get("image"))
        rec: dict[str, Any] = {
            "id": name, "aliases": aliases.get(name, []), "display_name": name, "tags": [], "parent_id": None,
            "description": ep.get("description"), "tasks": ep.get("tasks") or [], "kind": ep.get("kind"),
            "engine": ep.get("engine"), "contract": ep.get("contract"), "api": ep.get("api"),
            "signature": ep.get("signature"), "gpu": {"fraction": ep.get("gpu_fraction")} if ep.get("gpu_fraction")
            else None, "state": "running", "state_detail": None, "leased_by": [], "in_flight": 0,
            "build_key": build_key, "image": ep.get("image"), "capabilities": None, "cold_start_s": 10.0,
            "cold_start_measured": False, "endpoint": None, "last_used": None, "calls": 0}
        check = checks.get(name)
        if check and check.get("capabilities"):
            rec["capabilities"] = {
                "build_key": build_key, "status": "verified", "usable_as": usable_as(rec),
                "tasks": list(check["capabilities"]), "timing_level": check.get("timing_level"), "output_detail": [],
                "protocol": {"contract": ep.get("contract"), "api": ep.get("api")}, "rtf": None, "verified_at": None,
                "report": {"lines": [{"name": n, "status": st, "reason": "recorded"} for st, n in check.get("lines", [])]},
            }
        rec["registered"] = rec["capabilities"] is not None or ep.get("api") == "text.transform"
        records.append(rec)
    return FakeCatalog(records)


# ---------------------------------------------------------------- model records

_lock = threading.Lock()
_cache: dict[str, Any] = {"cat": None, "at": 0.0, "records": None, "error": None}


def clear_cache() -> None:
    with _lock:
        _cache.update(cat=None, at=0.0, records=None, error=None)


def _unavailable(cat: Any, exc: Exception) -> CatalogUnavailable:
    if isinstance(exc, CatalogError):
        return CatalogUnavailable(f"the Model Catalog at {cat.url} refused: {exc.message}")
    return CatalogUnavailable(f"the Model Catalog at {cat.url} did not answer ({type(exc).__name__})")


def models(force: bool = False) -> list[dict[str, Any]]:
    """Registered catalog models with their capability records and states (cached RECORDS_TTL_S)."""
    cat = get_catalog()
    with _lock:
        fresh = _cache["cat"] is cat and time.monotonic() - _cache["at"] < RECORDS_TTL_S
        if fresh and not force:
            if _cache["error"] is not None:  # a catalog that just failed is not asked again at once
                raise CatalogUnavailable(_cache["error"])
            if _cache["records"] is not None:
                return _cache["records"]
    try:
        records = cat.models()
    except (CatalogError, httpx.HTTPError) as exc:
        err = _unavailable(cat, exc)
        with _lock:
            _cache.update(cat=cat, at=time.monotonic(), records=None, error=str(err))
        raise err from None
    with _lock:
        _cache.update(cat=cat, at=time.monotonic(), records=records, error=None)
    return records


def record(model_id: str, force: bool = False) -> dict[str, Any]:
    """One model record by id or alias. KeyError when the catalog does not know it."""
    for rec in models(force):
        if rec["id"] == model_id or model_id in (rec.get("aliases") or []):
            return rec
    cat = get_catalog()
    try:
        return cat.model(model_id)  # not registered (or an alias the list did not name)
    except CatalogError as exc:
        if exc.status == 404:
            raise KeyError(f"the Model Catalog has no model {model_id!r}") from None
        raise _unavailable(cat, exc) from None
    except httpx.HTTPError as exc:
        raise _unavailable(cat, exc) from None


def states() -> dict[str, str]:
    """Model id -> state (running, stopped, ...); {} when the catalog does not answer."""
    try:
        return {r["id"]: r.get("state") or "stopped" for r in models()}
    except CatalogUnavailable:
        return {}


def capabilities(rec: dict[str, Any]) -> tuple[list[str], str | None, str | None]:
    """(tasks Label can use, timing level, why the model is not usable or None) from a record's capability
    record. A `stale` record (verified on an earlier build) still says what the model does."""
    caps = rec.get("capabilities") or None
    if caps is None:
        return [], None, "not verified yet"
    status = caps.get("status")
    if status == "failed":
        return [], None, "verification failed (see the catalog's report)"
    tasks = [t for t in caps.get("tasks") or [] if t in TASKS]
    if not tasks:
        if status in ("pending", "running"):
            return [], None, f"verification {status}"
        other = [TASK_NAMES.get(t, t) for t in caps.get("tasks") or []]
        return [], None, f"{', '.join(other)}: not a task Label runs" if other else "no speech task detected"
    if usable_as(rec) not in USABLE:
        return [], None, f"{rec.get('contract')} / {rec.get('api')} is not a protocol Label calls"
    return tasks, caps.get("timing_level"), None


def groups(rec: dict[str, Any]) -> list[str]:
    """The Models page groups a record belongs in (`GROUPS` keys), from every task its verifier found, also
    ones Label does not run; [] before verification, when it failed, or for tasks no group holds."""
    caps = rec.get("capabilities") or {}
    if caps.get("status") == "failed":
        return []
    found = set(caps.get("tasks") or [])
    return [g for g, tasks in GROUPS.items() if found & set(tasks)]


def sync_model(session: Session, rec: dict[str, Any]) -> Model:
    """The `model` row of a catalog record, created on first use and refreshed from the record."""
    m = session.scalar(select(Model).where(Model.catalog_id == rec["id"]))
    if m is None:
        m = Model(catalog_id=rec["id"])
        session.add(m)
    tasks, timing, _ = capabilities(rec)
    m.display_name = rec.get("display_name") or rec["id"]
    m.tags = list(rec.get("tags") or [])
    m.capabilities, m.timing_level = tasks, timing
    m.contract, m.api = rec.get("contract"), rec.get("api")
    m.build_key = rec.get("build_key")
    m.record = {k: v for k, v in rec.items() if k not in ("leased_by", "in_flight", "last_used", "calls")}
    m.synced_at = utcnow()
    session.flush()
    return m


def row_record(m: Model) -> dict[str, Any]:
    """A record rebuilt from a row's cache (the catalog is down): last read record, state `unknown`."""
    rec = {**(m.record or {}), "id": m.catalog_id, "display_name": m.display_name, "tags": m.tags,
           "contract": m.contract, "api": m.api, "build_key": m.build_key, "state": "unknown"}
    if not rec.get("capabilities") and m.capabilities:
        from ..adapters import adapter_class

        cls = adapter_class(m.contract or "", m.api or "")
        kind = None if cls is None else "segmenter" if cls.kind == "seg" else \
            "asr" if cls.api == "audio.transcriptions" else "chat-audio"
        rec["capabilities"] = {"status": "cached", "usable_as": kind, "tasks": list(m.capabilities),
                               "timing_level": m.timing_level}
    return rec


def ensure_model(session: Session, ref: int | str) -> Model:
    """The row for a model id (int, a row Label has) or a catalog model id (str, created on first use).
    A known row is refreshed when the catalog answers and used as it is when it does not.
    KeyError: unknown model; CatalogUnavailable: a catalog id while the catalog is down."""
    if isinstance(ref, int):
        m = session.get(Model, ref)
        if m is None:
            raise KeyError(f"model {ref} not found")
        try:
            return sync_model(session, record(m.catalog_id))
        except (KeyError, CatalogUnavailable):
            return m
    existing = session.scalar(select(Model).where(Model.catalog_id == ref))
    try:
        return sync_model(session, record(ref))
    except CatalogUnavailable:
        if existing is not None:
            return existing
        raise


def current_build(m: Model, force: bool = False) -> dict[str, Any]:
    """{build_key, image} of the model's current build: from the catalog, else what the row last saw."""
    try:
        rec = record(m.catalog_id, force)
        return {"build_key": rec.get("build_key"), "image": rec.get("image")}
    except (KeyError, CatalogUnavailable):
        return {"build_key": m.build_key, "image": (m.record or {}).get("image")}


# ---------------------------------------------------------------- leases

def holder_for(job_id: int) -> str:
    return f"{APP}:job-{job_id}"


@contextmanager
def leased(holder: str, model_ids: list[str], log: Log = print, on_wait: OnWait | None = None,
           on_active: Callable[[], None] | None = None) -> Iterator[Any]:
    """A lease on these models for the block, released after it. While the catalog queues the request,
    `on_wait(lease)` hears its position and what it waits for. ServicesBlocked when it cannot be had."""
    cat = get_catalog()
    log(f"lease {', '.join(model_ids)} for {holder}")
    try:
        lease = cat.lease(holder, list(model_ids), on_wait=on_wait)
    except LeaseFailed as exc:
        raise ServicesBlocked(exc.message) from None
    except CatalogError as exc:
        raise ServicesBlocked(f"the Model Catalog refused the lease: {exc.message}") from None
    except httpx.HTTPError as exc:
        raise ServicesBlocked(f"the Model Catalog at {cat.url} did not answer ({type(exc).__name__}); "
                              "start it with `just catalog-up` in the platform repository") from None
    log(f"lease {lease.id} active: {', '.join(model_ids)}")
    if on_active:
        on_active()
    try:
        yield lease
    finally:
        lease.release()
        log(f"released {', '.join(model_ids)}")


# ---------------------------------------------------------------- this app in the catalog

def register_app() -> bool:
    """Tell the catalog where other apps read Label's outputs (POST /api/apps). False when it did not answer."""
    s = get_settings()
    try:
        get_catalog().register_app(APP, s.public_base_url)
    except (CatalogError, httpx.HTTPError) as exc:
        log.warning("could not register %s with the Model Catalog at %s: %s", s.public_base_url, s.catalog_root, exc)
        return False
    return True


def register_app_in_background(retry_s: float = 60.0) -> threading.Thread:
    """register_app() now, and again every `retry_s` until the catalog answers; never blocks startup."""
    def loop() -> None:
        while not register_app():
            time.sleep(retry_s)

    t = threading.Thread(target=loop, name="catalog register_app", daemon=True)
    t.start()
    return t
