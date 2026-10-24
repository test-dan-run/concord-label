"""Models, read from the model catalog (platform/catalog/README.md).

Registering, verifying and serving models is the catalog's job, in its own UI (`CATALOG_UI_URL`). Label lists
the catalog's registered models with their state, capabilities, cold start and build key (and the groups the
Models page shows them in: ASR, VAD / diarisation, speech enhancement), and keeps a row
for each model it has used (platform/catalog.py). While the catalog does not answer, the list falls back to
those rows, marked as not reachable.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..adapters import adapter_class
from ..core.db import get_session
from ..core.models import Model, Run, RunTask
from ..core.settings import get_settings
from ..platform import catalog

router = APIRouter(tags=["models"])


def label_runs(session: Session) -> dict[int, int]:
    """Model id -> how many label runs used it, as segmenter or ensemble member."""
    counts: dict[int, int] = {}
    for p in session.scalars(select(Run.params).where(Run.task == RunTask.label)):
        for mid in {p.get("seg_model_id"), *(p.get("asr_model_ids") or [])}:
            if isinstance(mid, int):
                counts[mid] = counts.get(mid, 0) + 1
    return counts


def model_json(rec: dict[str, Any], row: Model | None, runs: dict[int, int]) -> dict[str, Any]:
    """A catalog record as the pages use it. `id` is Label's model id (null until a run first uses it);
    pickers send `catalog_id`."""
    tasks, timing, why = catalog.capabilities(rec)
    if why is None and adapter_class(rec.get("contract") or "", rec.get("api") or "") is None:
        tasks, why = [], f"no adapter for {rec.get('contract')} / {rec.get('api')}"
    caps = rec.get("capabilities") or {}
    stages = (["seg"] if {"vad", "diar"} & set(tasks) else []) + (["asr"] if "asr" in tasks else [])
    ui = get_settings().catalog_ui
    return {"id": row.id if row else None, "catalog_id": rec["id"], "display_name": rec.get("display_name") or rec["id"],
            "tags": rec.get("tags") or [], "description": rec.get("description"), "contract": rec.get("contract"),
            "api": rec.get("api"), "engine": rec.get("engine"), "image": rec.get("image"),
            "state": rec.get("state") or "unknown", "state_detail": rec.get("state_detail"),
            "leased_by": rec.get("leased_by") or [], "build_key": rec.get("build_key"),
            "capabilities": tasks, "timing_level": timing, "capabilities_status": caps.get("status"),
            "usable_as": catalog.usable_as(rec), "not_usable": why, "stages": stages,
            "groups": catalog.groups(rec),
            "cold_start_s": rec.get("cold_start_s"), "cold_start_measured": bool(rec.get("cold_start_measured")),
            "gpu": rec.get("gpu"), "runs": runs.get(row.id, 0) if row else 0,
            "catalog_url": f"{ui}/models/{rec['id']}"}


@router.get("/models")
def list_models(session: Session = Depends(get_session)) -> dict[str, Any]:
    """The catalog's registered models (`catalog.reachable` false: the rows Label has, from their last read)."""
    s = get_settings()
    rows = {m.catalog_id: m for m in session.scalars(select(Model))}
    runs = label_runs(session)
    status: dict[str, Any] = {"url": s.catalog_root, "ui_url": s.catalog_ui, "reachable": True, "error": None}
    try:
        records = catalog.models()
    except catalog.CatalogUnavailable as exc:
        status.update(reachable=False, error=str(exc))
        records = [catalog.row_record(m) for m in rows.values()]
    out = [model_json(r, rows.get(r["id"]), runs) for r in records]
    out.sort(key=lambda m: (not m["stages"], m["display_name"].lower()))
    return {"models": out, "catalog": status}
