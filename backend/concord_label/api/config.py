"""Config (CONTRACTS.md §1), tags, normalisers."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import peer
from ..core.db import get_session
from ..core.models import Dataset, Model
from ..core.settings import APP, CONTRACT_VERSION, VERSION, get_settings
from ..metrics import normalisers
from ..metrics.der import DEFAULT_COLLAR

router = APIRouter(tags=["config"])

@router.get("/config")
def get_config() -> dict[str, Any]:
    s = get_settings()
    return {
        "app": APP,
        "version": VERSION,
        "contract_version": CONTRACT_VERSION,
        # Concord Bench (SPLIT_PLAN.md §5): BENCH_URL, and whether it answered /api/config (cached ~30 s).
        "peer": peer.status(),
        "default_normaliser": normalisers.DEFAULT,
        "default_collar": DEFAULT_COLLAR,
        "collars": [0.0, 0.1, 0.25, 0.5],
        "replay": s.replay_dir is not None,
        "no_evict": s.no_evict,
        "links": {"platform_readme": "dev/README.md", "contracts_readme": "platform/contracts/README.md"},
    }


@router.get("/normalisers")
def list_normalisers() -> list[dict[str, Any]]:
    from ..registry.reader import text_transform_services

    out = [{"id": n.id, "key": n.key, "label": n.label, "description": n.description, "default": n.id ==
            normalisers.DEFAULT, "service": None} for n in normalisers.BUILTIN.values()]
    for svc in text_transform_services():
        n = normalisers.platform_normaliser(svc)
        out.append({"id": n.id, "key": n.key, "label": n.label, "description": n.description, "default": False,
                    "service": svc})
    return out


@router.get("/tags")
def list_tags(session: Session = Depends(get_session)) -> list[dict[str, Any]]:
    """Every tag used on datasets and models, most used first, for the tag inputs' suggestions."""
    counts: dict[str, int] = {}
    for tags in session.scalars(select(Dataset.tags)):
        for t in tags or []:
            counts[t] = counts.get(t, 0) + 1
    for tags in session.scalars(select(Model.tags)):
        for t in tags or []:
            counts[t] = counts.get(t, 0) + 1
    return [{"tag": t, "count": n} for t, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]
