"""Reference packages and the links from Concord Bench (SPLIT_PLAN.md §4, §5; CONTRACTS.md §2, §3, §5): reference
packages built for download, the file lookup by sha256 that "Open in Label" uses, and flags coming back. The preview
and the history also serve Publish to Dataset Catalog (api/dataset_catalog.py).

Contract version 3 removed the push to Bench ("Send to Bench"): datasets reach Bench through the Dataset Catalog.
Sends from before stay in the history as `push` rows."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.db import get_session
from ..core.models import AudioFile, Dataset, ReferenceExport
from ..label import flags, publish, refpkg
from ..platform import datasets as dataset_catalog
from ..runs import jobs
from .datasets import _clean_tags, get_dataset, job_brief

router = APIRouter(tags=["bench"])


@router.get("/datasets/{dataset_id}/reference-preview")
def reference_preview(dataset_id: int, scope: str = "complete", session: Session = Depends(get_session)) -> dict:
    """What a package or a published version would hold: counts, the references file and the files left out."""
    ds = get_dataset(session, dataset_id)
    try:
        pv = refpkg.preview(session, ds, scope)
    except refpkg.RefPkgError as exc:
        raise HTTPException(400, str(exc)) from None
    # With the Dataset Catalog set up, the panel publishes there (DATASET_CATALOG_PLAN.md §8.2).
    cat = publish.status(ds) if dataset_catalog.enabled() else None
    return {**pv, "dataset": {"id": ds.id, "name": ds.name, "uid": ds.uid, "tags": ds.tags}, "catalog": cat}


class SendIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    scope: str = "complete"
    tags: list[str] = Field(default_factory=list)
    include_audio: str = "auto"  # auto (every file) | always | never
    include_outputs: bool = False
    destination: str | None = None  # download (the only one since contract version 3)


@router.post("/datasets/{dataset_id}/reference-exports", status_code=202)
def build_package(dataset_id: int, body: SendIn, session: Session = Depends(get_session)) -> dict[str, Any]:
    """A reference package zip to download (CONTRACTS.md §2)."""
    ds = get_dataset(session, dataset_id)
    if body.scope not in refpkg.SCOPES:
        raise HTTPException(422, f"scope must be one of {', '.join(refpkg.SCOPES)}")
    if body.include_audio not in refpkg.AUDIO_CHOICES:
        raise HTTPException(422, f"include_audio must be one of {', '.join(refpkg.AUDIO_CHOICES)}")
    if body.destination == "push":
        raise HTTPException(422, "Concord Label no longer pushes packages to Concord Bench (contract version 3): "
                                 "publish the dataset to the Dataset Catalog, and import the version in Bench")
    if body.destination not in (None, "download"):
        raise HTTPException(422, "destination must be download")
    sel = refpkg.select_files(session, ds, body.scope)
    if sel.problem:
        raise HTTPException(422, sel.problem)
    rec = ReferenceExport(dataset_id=ds.id, name=body.name.strip(), scope=body.scope, tags=_clean_tags(body.tags),
                          include_audio=body.include_audio, include_outputs=body.include_outputs,
                          destination="download", status="queued")
    session.add(rec)
    session.flush()
    job = jobs.enqueue(session, "reference", {"export_id": rec.id, "dataset_id": ds.id})
    rec.job_id = job.id
    session.commit()
    return {"export": refpkg.export_json(rec), "job": job_brief(job)}


@router.get("/datasets/{dataset_id}/reference-exports")
def list_reference_exports(dataset_id: int, session: Session = Depends(get_session)) -> list[dict[str, Any]]:
    get_dataset(session, dataset_id)
    rows = session.scalars(select(ReferenceExport).where(ReferenceExport.dataset_id == dataset_id)
                           .order_by(ReferenceExport.id.desc()))
    return [refpkg.export_json(r) for r in rows]


def _export(session: Session, export_id: int) -> ReferenceExport:
    rec = session.get(ReferenceExport, export_id)
    if rec is None:
        raise HTTPException(404, "reference export not found")
    return rec


@router.get("/reference-exports/{export_id}")
def reference_export(export_id: int, session: Session = Depends(get_session)) -> dict[str, Any]:
    return refpkg.export_json(_export(session, export_id))


@router.get("/reference-exports/{export_id}/download")
def download_reference_export(export_id: int, session: Session = Depends(get_session)) -> FileResponse:
    rec = _export(session, export_id)
    if rec.status != "done" or not rec.path or not Path(rec.path).is_file():
        raise HTTPException(404, "no package file")
    return FileResponse(rec.path, media_type="application/zip", filename=Path(rec.path).name)


# ---------------------------------------------------------------- CONTRACTS.md §5

@router.get("/files/by-sha/{sha256}")
def file_by_sha(sha256: str, catalog_dataset_id: str | None = None, session: Session = Depends(get_session)) -> dict:
    """The file with these original bytes. With `catalog_dataset_id`, within the Label datasets of that Dataset
    Catalog dataset, the working dataset first (CONTRACTS.md §3, §5); else in any dataset, the newest first."""
    stmt = select(AudioFile).where(AudioFile.sha256 == sha256.lower())
    if catalog_dataset_id:
        stmt = stmt.join(Dataset).where(Dataset.catalog_dataset_id == catalog_dataset_id, Dataset.status != "failed") \
            .order_by(Dataset.catalog_version.desc().nulls_last(), Dataset.id.desc(), AudioFile.id.desc())
    else:
        stmt = stmt.order_by(AudioFile.id.desc())
    f = session.scalars(stmt).first()
    if f is None:
        where = (f" in a dataset made from or published to the Dataset Catalog dataset {catalog_dataset_id}"
                 if catalog_dataset_id else "")
        raise HTTPException(404, f"no file with sha256 {sha256[:12]}…{where}")
    return {"file_id": f.id, "dataset_id": f.dataset_id}


@router.post("/flags/import")
def import_flags(body: dict[str, Any], session: Session = Depends(get_session)) -> dict[str, Any]:
    try:
        out = flags.import_flags(session, body)
    except flags.FlagsError as exc:
        session.rollback()
        raise HTTPException(400, str(exc)) from None
    except flags.UnknownSource as exc:
        session.rollback()
        raise HTTPException(404, str(exc)) from None
    session.commit()
    return out
