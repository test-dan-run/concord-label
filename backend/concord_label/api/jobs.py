"""Jobs (the queue the worker runs), and the JSON of a label run."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.db import get_session
from ..core.models import Dataset, Job, JobStatus, Run
from ..runs import jobs
from .datasets import job_brief

router = APIRouter(tags=["jobs"])
ACTIVE = ("queued", "running", "waiting")


def run_label(run: Run) -> str:
    n = len(run.params.get("asr_model_ids") or [])
    return f"Pseudo-label · {n} ASR model{'s' if n != 1 else ''}"


def run_json(run: Run) -> dict[str, Any]:
    return {"id": run.id, "task": run.task, "label": run_label(run), "dataset_id": run.dataset_id,
            "dataset": run.dataset.name, "status": run.status, "error": run.error, "image_tags": run.image_tags,
            "params": run.params, "rtf": run.rtf_by_stage, "created_at": run.created_at.isoformat(),
            "started_at": run.started_at.isoformat() if run.started_at else None,
            "finished_at": run.finished_at.isoformat() if run.finished_at else None}


@router.get("/jobs")
def list_jobs(status: str | None = None, active: bool = False, limit: int = 50,
              session: Session = Depends(get_session)) -> list[dict[str, Any]]:
    stmt = select(Job).order_by(Job.id.desc()).limit(limit)
    if active:
        stmt = stmt.where(Job.status.in_(ACTIVE))
    elif status:
        stmt = stmt.where(Job.status == status)
    out = []
    for j in session.scalars(stmt):
        d = job_brief(j)
        d["label"] = job_label(session, j)
        out.append(d)
    return out


def job_label(session: Session, j: Job) -> str:
    p = j.payload or {}
    if j.type == "ingest":
        d = session.get(Dataset, p.get("dataset_id"))
        return f"ingest · {d.name}" if d else "ingest"
    if j.type == "check":
        return f"check · {p.get('service')} (moved to the model catalog)"
    if j.type == "label":
        r = session.get(Run, p.get("run_id"))
        return f"pseudo-label · {r.dataset.name}" if r else "pseudo-label"
    if j.type == "reference":
        d = session.get(Dataset, p.get("dataset_id"))
        return f"send to Bench · {d.name}" if d else "send to Bench"
    if j.type == "export":
        kind = "full kit" if p.get("kind") == "full" else "model kit"
        return f"offline {kind} · {', '.join(p.get('services') or [])}"
    return j.type


@router.get("/jobs/{job_id}")
def job_detail(job_id: int, session: Session = Depends(get_session)) -> dict[str, Any]:
    j = session.get(Job, job_id)
    if j is None:
        raise HTTPException(404, "job not found")
    d = job_brief(j)
    d["label"] = job_label(session, j)
    d["log"] = jobs.read_log(job_id)
    return d


@router.post("/jobs/{job_id}/retry")
def retry_job(job_id: int, session: Session = Depends(get_session)) -> dict[str, Any]:
    j = session.get(Job, job_id)
    if j is None:
        raise HTTPException(404, "job not found")
    if j.status not in (JobStatus.failed, JobStatus.blocked):
        raise HTTPException(409, f"job is {j.status}")
    jobs.retry(session, j)
    session.commit()
    return job_brief(j)
