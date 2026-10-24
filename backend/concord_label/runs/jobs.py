"""The job queue: one worker process reading the `job` table (no Redis / Celery).

Job types:
- ingest  {dataset_id, source, cleanup}         copy, decode, derive, parse references
- label   {run_id}                              ensemble pseudo-labelling (Phase 2), writes segments
- reference {export_id, dataset_id[, force]}   Send to Bench: write a reference package, push it if asked; or
                                                Publish to Dataset Catalog (destination `catalog`, label/publish.py)
- catalog-dataset {dataset_id, catalog_dataset_id, version}
                                                New from Dataset Catalog: a dataset from a catalog version

Models are the model catalog's business (platform/catalog.py). A job that needs models takes leases from
the catalog itself, one per stage (label/pseudo.py), as `label:job-<id>`; a leased model is never stopped,
whichever app asks. While a lease waits in the catalog's queue the job is `waiting`, with its queue
position and what it waits for (`JobContext.waiting`); a lease that fails, or a catalog that does not
answer, makes the job `blocked` with the catalog's message. `required_services` lists the models a job
has work for: among queued jobs, the worker takes the oldest whose models are all running already.
"""

from __future__ import annotations

import json
import shutil
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..core import paths
from ..core.db import SessionLocal
from ..core.models import Dataset, Job, JobStatus, ReferenceExport, Run, utcnow
from ..platform import catalog


class JobContext:
    """Log lines and progress go to files beside the job, never to the database: the job's own
    session may hold SQLite's single write lock, and a second writer would wait on it."""

    def __init__(self, job_id: int):
        self.job_id = job_id
        self.log_path = paths.job_log(job_id)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._progress = 0.0
        self._message = ""
        self._waiting: dict[str, Any] | None = None

    def _write_progress(self) -> None:
        p = paths.job_progress(self.job_id)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps({"progress": self._progress, "message": self._message[:300],
                                   "waiting": self._waiting}))
        tmp.replace(p)

    def log(self, msg: str) -> None:
        with open(self.log_path, "a", encoding="utf-8") as fh:
            fh.write(f"{datetime.now():%H:%M:%S} {msg}\n")
        self._message = msg
        self._write_progress()

    def progress(self, p: float, msg: str | None = None) -> None:
        self._progress = max(0.0, min(1.0, p))
        if msg:
            self._message = msg
        self._write_progress()

    def waiting(self, lease: dict[str, Any]) -> None:
        """The job's lease is queued in the catalog: the job is `waiting`, with its position and reason."""
        self._waiting = {"models": lease.get("models") or [], "position": lease.get("position"),
                         "waiting_for": lease.get("waiting_for"), "lease_state": lease.get("state")}
        what = lease.get("waiting_for") or lease.get("state") or "compute"
        pos = f" (#{lease['position']} in the queue)" if lease.get("position") else ""
        self.log(f"waiting for {', '.join(self._waiting['models'])}{pos}: {what}")
        self._set_status(JobStatus.waiting)

    def active(self) -> None:
        """The lease became active: the job runs again."""
        if self._waiting is not None:
            self._waiting = None
            self._write_progress()
            self._set_status(JobStatus.running)

    def _set_status(self, status: str) -> None:
        # A short session of its own: the handler's session has committed before it asked for a lease.
        try:
            with SessionLocal() as s:
                s.execute(update(Job).where(Job.id == self.job_id, Job.status.in_((JobStatus.running, JobStatus.waiting)))
                          .values(status=status))
                s.commit()
        except Exception:  # noqa: BLE001 - the status is cosmetic; the progress file still says it
            pass

    @property
    def message(self) -> str:
        return self._message


def live_progress(job_id: int) -> dict[str, Any] | None:
    """Progress of a running job, from its progress file."""
    try:
        return json.loads(paths.job_progress(job_id).read_text())
    except (OSError, ValueError):
        return None


def enqueue(session: Session, type_: str, payload: dict[str, Any], required_services: list[str] | None = None) -> Job:
    job = Job(type=type_, payload=payload, required_services=sorted(set(required_services or [])))
    session.add(job)
    session.flush()
    return job


def read_log(job_id: int, tail: int = 400) -> list[str]:
    p = paths.job_log(job_id)
    if not p.exists():
        return []
    return p.read_text(encoding="utf-8", errors="replace").splitlines()[-tail:]


# ---------------------------------------------------------------- scheduling

def recover(session: Session) -> int:
    """After a crash: running or waiting jobs go back to the queue (their leases expire on their own)."""
    res = session.execute(update(Job).where(Job.status.in_((JobStatus.running, JobStatus.waiting)))
                          .values(status=JobStatus.queued))
    return res.rowcount or 0


def claim_next(session: Session) -> Job | None:
    """The next job: the oldest whose models are all running in the catalog (no start, no eviction), else the
    oldest. Without a catalog answer, the oldest."""
    queued = session.scalars(select(Job).where(Job.status == JobStatus.queued).order_by(Job.id)).all()
    if not queued:
        return None
    job = queued[0]
    if any(j.required_services for j in queued):
        state = catalog.states()
        job = next((j for j in queued if all(state.get(n) == "running" for n in j.required_services)), queued[0])
    job.status, job.started_at, job.attempts = JobStatus.running, utcnow(), job.attempts + 1
    session.commit()
    return job


# ---------------------------------------------------------------- handlers

def _handle_ingest(session: Session, job: Job, ctx: JobContext) -> dict[str, Any]:
    from ..datasets.ingest import ingest

    p = job.payload
    source = Path(p["source"])
    try:
        report = ingest(session, p["dataset_id"], source, ctx.progress)
        session.commit()
    except Exception:
        session.rollback()
        ds = session.get(Dataset, p["dataset_id"])
        if ds is not None:
            ds.status = "failed"
            session.commit()
        raise
    if p.get("cleanup") and source.is_relative_to(paths.data_root() / "uploads"):
        shutil.rmtree(source, ignore_errors=True)
    ctx.log(f"ingested {report['files']} files ({report['hours'] * 60:.1f} min), {report['usable_files']} usable")
    return {"dataset_id": p["dataset_id"]}


def _handle_label(session: Session, job: Job, ctx: JobContext) -> dict[str, Any]:
    from ..label import pseudo

    run = session.get(Run, job.payload["run_id"])
    if run is None:
        raise ValueError(f"run {job.payload['run_id']} not found")
    try:
        pseudo.execute(session, run, ctx.log, ctx.progress, pseudo.Leases(holder_for(job.id), ctx))
        run.status = "done"
        session.commit()
    except Exception as exc:
        session.rollback()
        run = session.get(Run, job.payload["run_id"])
        run.status, run.error = "failed", str(exc)[:2000]
        session.commit()
        raise
    return {"run_id": run.id, "summary": {k: v for k, v in run.outputs["summary"].items() if k != "agreement"}}


def _handle_reference(session: Session, job: Job, ctx: JobContext) -> dict[str, Any]:
    from ..label import publish, refpkg

    p = job.payload
    rec = session.get(ReferenceExport, p["export_id"])
    if rec is None:
        raise ValueError(f"reference export {p['export_id']} not found")
    rec.status, rec.error = "running", None
    session.commit()
    try:
        if rec.destination == "catalog":
            publish.run(session, rec, bool(p.get("force")), ctx.log, ctx.progress)
        else:
            refpkg.run(session, rec, ctx.log, ctx.progress)
        session.commit()
    except Exception as exc:
        session.rollback()
        rec = session.get(ReferenceExport, p["export_id"])
        rec.status, rec.error, rec.finished_at = "failed", str(exc)[:2000], utcnow()
        session.commit()
        raise
    return {"export_id": rec.id, "version": rec.version, "destination": rec.destination,
            "bench_import_id": rec.bench_import_id, "counts": rec.counts}


def _handle_catalog_dataset(session: Session, job: Job, ctx: JobContext) -> dict[str, Any]:
    from ..label import from_catalog

    p = job.payload
    try:
        report = from_catalog.ingest_version(session, p["dataset_id"], ctx.progress)
        session.commit()
    except Exception:
        session.rollback()
        ds = session.get(Dataset, p["dataset_id"])
        if ds is not None:
            ds.status = "failed"
            session.commit()
        raise
    ctx.log(f"made from v{p['version']} of {p['catalog_dataset_id']}: {report['files']} files "
            f"({report['hours'] * 60:.1f} min), {report['segments']} segments")
    return {"dataset_id": p["dataset_id"]}


HANDLERS = {"ingest": _handle_ingest, "label": _handle_label, "reference": _handle_reference,
            "catalog-dataset": _handle_catalog_dataset}
RUN_JOBS = ("label",)  # job types whose payload names a `run` that mirrors the job's state


def holder_for(job_id: int) -> str:
    """The catalog lease holder of a job."""
    return catalog.holder_for(job_id)


def _blocked(session: Session, job_id: int, ctx: JobContext, exc: Exception) -> None:
    ctx.log(f"blocked: {exc}")
    job = session.get(Job, job_id)
    job.status, job.error, job.finished_at = JobStatus.blocked, str(exc)[:2000], utcnow()
    job.message = ctx.message[:300] or None
    if job.type in RUN_JOBS:
        run = session.get(Run, job.payload["run_id"])
        if run is not None:
            run.status, run.error = "blocked", str(exc)[:2000]
    session.commit()


def run_job(job_id: int) -> None:
    ctx = JobContext(job_id)
    session = SessionLocal()
    try:
        job = session.get(Job, job_id)
        assert job is not None
        try:
            result = HANDLERS[job.type](session, job, ctx)
        except catalog.ServicesBlocked as exc:
            session.rollback()
            _blocked(session, job_id, ctx, exc)
            return
        job = session.get(Job, job_id)
        job.status, job.result, job.progress, job.finished_at = JobStatus.done, result, 1.0, utcnow()
        job.message = ctx.message[:300] or None
        session.commit()
    except Exception as exc:  # noqa: BLE001 - recorded on the job
        session.rollback()
        ctx.log("failed: " + "".join(traceback.format_exception_only(exc)).strip())
        with open(ctx.log_path, "a", encoding="utf-8") as fh:
            fh.write(traceback.format_exc())
        job = session.get(Job, job_id)
        job.status, job.error, job.finished_at = JobStatus.failed, str(exc)[:2000], utcnow()
        job.message = ctx.message[:300] or None
        session.commit()
    finally:
        session.close()


def run_pending(max_jobs: int | None = None) -> int:
    """Run queued jobs in this process until the queue is empty (tests, `concord worker --once`)."""
    done = 0
    while max_jobs is None or done < max_jobs:
        session = SessionLocal()
        try:
            job = claim_next(session)
        finally:
            session.close()
        if job is None:
            break
        run_job(job.id)
        done += 1
    return done


def retry(session: Session, job: Job) -> None:
    job.status, job.error, job.progress, job.message = JobStatus.queued, None, 0.0, None
    if job.type in RUN_JOBS:
        run = session.get(Run, job.payload["run_id"])
        if run is not None:
            run.status, run.error = "queued", None
    if job.type == "reference":
        ref = session.get(ReferenceExport, job.payload["export_id"])
        if ref is not None:
            ref.status, ref.error, ref.finished_at = "queued", None, None
