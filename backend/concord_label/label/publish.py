"""Publish to Dataset Catalog (DATASET_CATALOG_PLAN.md §8.2), which replaced Send to Bench in contract version 3:
Concord Bench imports the published versions.

A publish is a `reference_export` row with destination `catalog`, run as a `reference` job like a package:

1. The first publish of a dataset makes a catalog dataset and keeps its id in `dataset.catalog_dataset_id`.
2. The store gets any audio it lacks (audio that came through Sync is still in data/originals/), and Label holds
   every file of the dataset.
3. Label writes the version folder with refpkg's code, without audio/ and outputs/, and publishes it with the
   version the dataset is based on (`dataset.catalog_version`) as its parent.

The scope, the references file and the labels follow refpkg.select_files, as for a reference package. When someone
published a newer version than the dataset is based on, the catalog refuses the parent (409). The API checks that
first and asks the person (`stale`), and a publish they confirm goes with `force`.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core import paths
from ..core.models import AudioFile, Dataset, ReferenceExport, utcnow
from ..platform import datasets as dataset_catalog
from . import refpkg

Log = refpkg.Log
Progress = refpkg.Progress


def version_brief(v: dict[str, Any] | None) -> dict[str, Any] | None:
    """What the panels show of a catalog version."""
    if not v:
        return None
    return {"n": v["n"], "parent_n": v.get("parent_n"), "published_by": v.get("published_by"),
            "published_at": v.get("published_at"), "label_types": v.get("label_types") or [],
            "counts": v.get("counts") or {}, "url": dataset_catalog.ui_url(v["dataset_id"], v["n"])}


def status(ds: Dataset) -> dict[str, Any]:
    """The catalog side of the publish preview: the catalog dataset, the version this dataset is based on, the
    latest version, and whether a publish would be stale (someone published after `based_on`)."""
    out: dict[str, Any] = {"dataset_id": ds.catalog_dataset_id, "name": None, "based_on": ds.catalog_version,
                           "latest": None, "stale": False, "deleted": False, "error": None,
                           "url": dataset_catalog.ui_url(ds.catalog_dataset_id) if ds.catalog_dataset_id else None}
    if not ds.catalog_dataset_id:
        return out
    try:
        with dataset_catalog.calls("reading the catalog dataset"):
            d = dataset_catalog.get().dataset(ds.catalog_dataset_id)
    except dataset_catalog.Unavailable as exc:
        if exc.status == 404:
            out["deleted"] = True  # deleted in the catalog: the next publish makes a new catalog dataset
        else:
            out["error"] = str(exc)
        return out
    out["name"] = d.get("name")
    out["latest"] = version_brief(d.get("latest"))
    out["stale"] = out["latest"] is not None and out["latest"]["n"] != ds.catalog_version
    return out


def run(session: Session, rec: ReferenceExport, force: bool = False, log: Log = print,
        progress: Progress = lambda p, m: None) -> None:
    """Publish the version of `rec`. Fills in `rec` and the dataset's catalog fields; raises on failure."""
    from aicore.datasets import StaleParent

    ds = session.get(Dataset, rec.dataset_id)
    if ds is None:
        raise refpkg.RefPkgError("the dataset no longer exists")
    dc = dataset_catalog.get()
    sel = refpkg.select_files(session, ds, rec.scope)
    if sel.problem:
        raise refpkg.RefPkgError(sel.problem)
    with dataset_catalog.calls("publishing"):
        _catalog_dataset(session, ds, rec, dc, log)
        rec.catalog_dataset_id = ds.catalog_dataset_id
        session.commit()
        progress(0.05, "checking the store")
        files = {f.sha256: f for f, _ in sel.files}
        missing = dc.missing(sorted(files))
        for k, sha in enumerate(missing):
            f = files[sha]
            src = paths.original(sha, f.ext)
            if not src.is_file():
                raise refpkg.RefPkgError(f"the original audio of {f.rel_path} is in neither the store nor this "
                                         "install's data folder")
            progress(0.05 + 0.75 * k / len(missing), f"uploading {f.rel_path}")
            dc.upload_audio(src, sha)
        if missing:
            log(f"uploaded {len(missing)} files the store did not have")
        dc.hold(sorted(set(session.scalars(select(AudioFile.sha256).where(AudioFile.dataset_id == ds.id)))))
        progress(0.85, "publishing")
        with tempfile.TemporaryDirectory(prefix="version-", dir=_tmp()) as tmp:
            manifest = refpkg.write_version(session, ds, sel, Path(tmp), name=rec.name, tags=rec.tags)
            try:
                v = dc.publish(ds.catalog_dataset_id, tmp, parent=ds.catalog_version, force=force)
            except StaleParent as exc:
                latest = exc.latest or {}
                raise refpkg.RefPkgError(
                    f"v{latest.get('n')} was published by {latest.get('published_by')} at "
                    f"{latest.get('published_at')}, after the version this dataset is based on "
                    f"({_v(ds.catalog_version)}). Publish again to choose whether to publish anyway.") from None
    ds.catalog_version = v["n"]
    rec.version, rec.manifest = v["n"], v.get("manifest") or manifest
    rec.counts = {**(v.get("counts") or manifest["counts"]), "uploaded": len(missing), "created": v["created"],
                  "left_out": sel.left_out, "labelled_files": len(refpkg.attributes(sel).get("files", {})),
                  "parent": v.get("parent_n")}
    rec.status, rec.error, rec.finished_at = "done", None, utcnow()
    if v["created"]:
        log(f"published v{v['n']} of {ds.catalog_dataset_id} ({rec.counts.get('files')} files, "
            f"{rec.counts.get('segments')} segments), parent {_v(v.get('parent_n'))}")
    else:
        log(f"v{v['n']} has the same content already; nothing new was published")
    progress(1.0, "done")


def _catalog_dataset(session: Session, ds: Dataset, rec: ReferenceExport, dc: Any, log: Log) -> None:
    """Make the catalog dataset on the first publish (or when it was deleted in the catalog); otherwise give it
    the name and tags chosen for this publish."""
    from aicore.datasets import NotFound

    if ds.catalog_dataset_id:
        try:
            cur = dc.dataset(ds.catalog_dataset_id)
        except NotFound:
            log(f"the catalog dataset {ds.catalog_dataset_id} was deleted; making a new one")
            ds.catalog_dataset_id, ds.catalog_version = None, None
        else:
            if cur.get("name") != rec.name or sorted(cur.get("tags") or []) != sorted(rec.tags):
                dc.update_dataset(ds.catalog_dataset_id, name=rec.name, tags=list(rec.tags))
            return
    made = dc.create_dataset(rec.name, language=ds.language, description=ds.description, tags=list(rec.tags))
    ds.catalog_dataset_id, ds.catalog_version = made["id"], None
    log(f"made the catalog dataset {made['id']} ({rec.name})")


def _tmp() -> str:
    d = paths.exports()
    d.mkdir(parents=True, exist_ok=True)
    return str(d)


def _v(n: int | None) -> str:
    return f"v{n}" if n else "none"
