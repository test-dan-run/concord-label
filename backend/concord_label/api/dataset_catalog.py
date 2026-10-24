"""The Dataset Catalog (DATASET_CATALOG_PLAN.md §8): its datasets and versions for "New from Dataset Catalog", a
working dataset made from a version, and Publish to Dataset Catalog. Each call answers 409 when the Dataset Catalog
is not set up (DATASET_STORE), and 502 when it doesn't answer."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..core.db import get_session
from ..core.models import Dataset, ReferenceExport
from ..label import publish, refpkg
from ..platform import datasets as dataset_catalog
from ..runs import jobs
from .datasets import _clean_tags, dataset_json, get_dataset, job_brief

router = APIRouter(tags=["dataset-catalog"])


def _require() -> None:
    if not dataset_catalog.enabled():
        raise HTTPException(409, "the Dataset Catalog is not set up (DATASET_STORE is not set)")


@contextmanager
def _catalog(what: str) -> Iterator[Any]:
    _require()
    try:
        with dataset_catalog.calls(what):
            yield dataset_catalog.get()
    except dataset_catalog.Unavailable as exc:
        raise HTTPException(404 if exc.status == 404 else 502, str(exc)) from None


def _dataset_brief(d: dict[str, Any]) -> dict[str, Any]:
    return {k: d.get(k) for k in ("id", "name", "language", "description", "tags", "created_by", "created_at",
                                  "updated_at")} | {"latest": publish.version_brief(d.get("latest")),
                                                    "url": dataset_catalog.ui_url(d["id"])}


@router.get("/dataset-catalog/datasets")
def catalog_datasets(q: str | None = None) -> list[dict[str, Any]]:
    """The catalog's datasets with their latest version, most recently changed first."""
    with _catalog("listing datasets") as dc:
        return [_dataset_brief(d) for d in dc.datasets(q=q or None)]


@router.get("/dataset-catalog/datasets/{catalog_id}")
def catalog_dataset(catalog_id: str) -> dict[str, Any]:
    """One catalog dataset with its versions, newest first."""
    with _catalog("reading the dataset") as dc:
        d = dc.dataset(catalog_id)
    return _dataset_brief(d) | {"versions": [publish.version_brief(v) for v in d.get("versions") or []]}


class FromCatalogIn(BaseModel):
    catalog_dataset_id: str = Field(min_length=1, max_length=32)
    version: int = Field(ge=1)
    name: str | None = Field(default=None, max_length=200)
    tags: list[str] | None = None


@router.post("/datasets/from-catalog", status_code=201)
def new_from_catalog(body: FromCatalogIn, session: Session = Depends(get_session)) -> dict[str, Any]:
    """A working dataset made from a catalog version, filled by a `catalog-dataset` job (label/from_catalog.py)."""
    with _catalog("reading the version") as dc:
        d = dc.dataset(body.catalog_dataset_id)
        v = dc.version(body.catalog_dataset_id, body.version)
    manifest = v.get("manifest") or {}
    ds = Dataset(name=(body.name or "").strip() or d["name"], language=d.get("language") or manifest.get("language"),
                 description=d.get("description"), tags=_clean_tags(body.tags if body.tags is not None
                                                                     else d.get("tags") or []),
                 status="ingesting", catalog_dataset_id=d["id"], catalog_version=v["n"])
    session.add(ds)
    session.flush()
    job = jobs.enqueue(session, "catalog-dataset", {"dataset_id": ds.id, "catalog_dataset_id": d["id"],
                                                    "version": v["n"]})
    session.commit()
    return {"dataset": dataset_json(session, ds), "job": job_brief(job)}


class PublishIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    scope: str = "complete"
    tags: list[str] = Field(default_factory=list)
    force: bool = False  # publish although someone published after the version this dataset is based on


@router.post("/datasets/{dataset_id}/catalog-publishes", status_code=202)
def publish_to_catalog(dataset_id: int, body: PublishIn, session: Session = Depends(get_session)) -> dict[str, Any]:
    ds = get_dataset(session, dataset_id)
    if body.scope not in refpkg.SCOPES:
        raise HTTPException(422, f"scope must be one of {', '.join(refpkg.SCOPES)}")
    _require()
    sel = refpkg.select_files(session, ds, body.scope)
    if sel.problem:
        raise HTTPException(422, sel.problem)
    st = publish.status(ds)
    if st["error"]:
        raise HTTPException(502, st["error"])
    if st["stale"] and not body.force:
        latest = st["latest"]
        raise HTTPException(409, f"v{latest['n']} was published by {latest['published_by']} at "
                                 f"{latest['published_at']}, after the version this dataset is based on "
                                 f"({'v' + str(ds.catalog_version) if ds.catalog_version else 'none'}); "
                                 "publish anyway with force")
    rec = ReferenceExport(dataset_id=ds.id, name=body.name.strip(), scope=body.scope, tags=_clean_tags(body.tags),
                          include_audio="never", include_outputs=False, destination="catalog", status="queued",
                          catalog_dataset_id=ds.catalog_dataset_id)
    session.add(rec)
    session.flush()
    job = jobs.enqueue(session, "reference", {"export_id": rec.id, "dataset_id": ds.id, "force": body.force})
    rec.job_id = job.id
    session.commit()
    return {"export": refpkg.export_json(rec), "job": job_brief(job)}
