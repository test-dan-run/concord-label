"""Phase 3 (Improve): retraining / fine-tuning. On hold, and installed in no image (SPLIT_PLAN.md §3.2).

On hold (PLAN §7, Q5): scope and launch mechanism are not confirmed, so this package only carries a
placeholder router. When Phase 3 is scoped, a concord-label-full image installs it and a feature flag
mounts the router at /api/improve. It may read `segment`, `dataset` and `model`, and will own
`training_run`; nothing in `concord_label` imports it (import-linter enforces this).
"""

from fastapi import APIRouter

router = APIRouter(tags=["improve"])


@router.get("/status")
def status() -> dict:
    return {"phase": 3, "state": "on hold", "reason": "Scope and training launch mechanism await the owner (Q5)."}
