"""Send to Bench: a dataset's reviewed labels as a reference package (CONTRACTS.md §2, SPLIT_PLAN.md §4).

What goes in follows the old Promote rules (PLAN.md §6.6):
- only **confirmed** segments; auto-accepted ones never become references;
- scope `complete` (default): only files where every segment is reviewed, so DER is meaningful;
- scope `any`: every file with a confirmed segment, ASR only;
- segments keep their own (unpadded) times.

The references file follows what the segments carry: speaker and text -> `refs/segments.seglst.json`,
text only (or scope `any`) -> `refs/segments.jsonl`, speaker only -> `refs/refs.rttm`. `refs/uids.jsonl`
carries every segment's uid so flags can come back (§3). `refs/attributes.json` carries each file's
metadata (from the annotations it was uploaded with) as file labels for Bench to slice by (§2.7). `outputs/seg.jsonl` optionally carries the
whole-file outputs of the label runs' seg model, with their params written out in full and the catalog
build key that made them (§2.5).

A package is one zip with a single top folder `<name>-refs-v<N>/`. It is either downloaded, or pushed to
Bench with its chunked upload API and staged there as a reference import (`push`).
"""

from __future__ import annotations

import csv
import io
import json
import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import peer
from ..adapters.base import params_hash
from ..core import paths
from ..core.models import (
    AudioFile,
    Dataset,
    Model,
    ModelOutput,
    ReferenceExport,
    Run,
    RunTask,
    Segment,
    SegmentStatus,
    utcnow,
)
from ..core.settings import APP
from ..cuts import usable_files
from ..metrics import normalisers
from ..platform import datasets as dataset_catalog
from ..runs import cache
from .sync import install_id

FORMAT = "concord-reference-package"
FORMAT_VERSION = 2
SCOPES = ("complete", "any")
AUDIO_CHOICES = ("auto", "always", "never")
CHUNK_BYTES = 8 * 1024 * 1024
MAX_RESUMES = 5

REFS_SEGLST = "segments.seglst.json"
REFS_JSONL = "segments.jsonl"
REFS_RTTM = "refs.rttm"

Log = Callable[[str], None]
Progress = Callable[[float, str], None]


class RefPkgError(ValueError):
    pass


# ---------------------------------------------------------------- what goes in

@dataclass
class Selection:
    scope: str
    files: list[tuple[AudioFile, list[Segment]]] = field(default_factory=list)
    left_out: int = 0  # files with confirmed segments that are not fully reviewed (scope complete)
    label_types: list[str] = field(default_factory=list)
    refs_file: str | None = None
    problem: str | None = None
    stats: dict[str, Any] = field(default_factory=dict)

    @property
    def segments(self) -> int:
        return sum(len(s) for _, s in self.files)

    @property
    def seconds(self) -> float:
        return sum(f.duration or 0.0 for f, _ in self.files)


def select_files(session: Session, ds: Dataset, scope: str = "complete") -> Selection:
    """The files and confirmed segments a package of `ds` would carry, and the references file for them."""
    if scope not in SCOPES:
        raise RefPkgError(f"scope must be one of {', '.join(SCOPES)}")
    sel = Selection(scope)
    files = usable_files(session, ds.id)
    statuses: Counter[str] = Counter()
    reviewed_files = 0
    for f in files:
        statuses.update(s.status for s in f.segments)
        complete = bool(f.segments) and all(s.reviewed for s in f.segments)
        reviewed_files += complete
        conf = sorted((s for s in f.segments if s.status == SegmentStatus.confirmed), key=lambda s: (s.start, s.end))
        if not conf:
            continue
        if scope == "complete" and not complete:
            sel.left_out += 1
            continue
        sel.files.append((f, conf))
    sel.stats = {"confirmed": statuses[SegmentStatus.confirmed], "auto": statuses[SegmentStatus.auto],
                 "needs_review": statuses[SegmentStatus.pseudo], "rejected": statuses[SegmentStatus.rejected],
                 "files": len(files), "files_fully_reviewed": reviewed_files}
    if not sel.files:
        sel.problem = "no files qualify: confirm some segments first" + (
            " (scope 'complete' needs every segment of a file reviewed)" if scope == "complete" else "")
        return sel
    segs = [s for _, ss in sel.files for s in ss]
    no_text = sum(1 for s in segs if s.text is None)
    no_spk = sum(1 for s in segs if not s.speaker)
    if scope == "any":
        if no_text:
            sel.problem = (f"{no_text} confirmed segment{'s have' if no_text != 1 else ' has'} no transcript: "
                           "scope 'any' sends transcripts (ASR only)")
        sel.label_types, sel.refs_file = ["asr"], REFS_JSONL
    elif no_text == 0 and no_spk == 0:
        sel.label_types, sel.refs_file = ["asr", "diar"], REFS_SEGLST
    elif no_text == 0:
        sel.label_types, sel.refs_file = ["asr"], REFS_JSONL
    elif no_spk == 0:
        sel.label_types, sel.refs_file = ["diar"], REFS_RTTM
    else:
        sel.problem = (f"{no_text} confirmed segments have no transcript and {no_spk} have no speaker; a package "
                       "needs every confirmed segment to carry a transcript, a speaker, or both")
    stems: dict[str, str] = {}
    dups = []
    for f, _ in sel.files:
        k = f.stem.lower()
        if k in stems:
            dups.append(f"{stems[k]} / {f.rel_path}")
        stems[k] = f.rel_path
    if dups and not sel.problem:
        sel.problem = ("file names must be unique ignoring folders and case (references are matched to audio by "
                       f"file name): {'; '.join(dups[:5])}")
    return sel


def next_version(session: Session, ds: Dataset) -> int:
    """export_version of the next package: one more than the packages already written from this dataset."""
    n = session.scalar(select(func.count(ReferenceExport.id)).where(ReferenceExport.dataset_id == ds.id,
                                                                   ReferenceExport.status == "done"))
    return (n or 0) + 1


def _label_runs(session: Session, ds: Dataset) -> list[Run]:
    return list(session.scalars(select(Run).where(Run.task == RunTask.label, Run.dataset_id == ds.id,
                                                  Run.status == "done").order_by(Run.id)))


def label_models(session: Session, ds: Dataset) -> list[str]:
    """Catalog model ids of the models the dataset's label runs used (seg model first, then ASR members)."""
    out: list[str] = []
    for r in _label_runs(session, ds):
        for mid in [r.params.get("seg_model_id"), *(r.params.get("asr_model_ids") or [])]:
            m = session.get(Model, mid) if mid else None
            if m is not None and m.catalog_id not in out:
                out.append(m.catalog_id)
    return out


def seg_outputs(session: Session, ds: Dataset, shas: list[str]) -> list[dict[str, Any]]:
    """Whole-file outputs of the label runs' seg models on these files (CONTRACTS.md §2.5). The params are
    the ones the run called the model with, written out in full; a cache row is taken only when its hash is
    the hash of those params, and only with a build key (rows from before the model catalog have none),
    which Bench matches on."""
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for r in reversed(_label_runs(session, ds)):
        m = session.get(Model, r.params.get("seg_model_id") or -1)
        params = r.params.get("seg_adapter")
        if m is None or not isinstance(params, dict):
            continue
        ph = params_hash(params)
        rows = session.scalars(select(ModelOutput).where(
            ModelOutput.model_name == m.catalog_id, ModelOutput.params_hash == ph,
            ModelOutput.build_key.is_not(None), ModelOutput.span_start == 0.0, ModelOutput.span_end == cache.WHOLE,
            ModelOutput.audio_sha256.in_(shas)).order_by(ModelOutput.audio_sha256, ModelOutput.id.desc()))
        run_image = ((r.image_tags or {}).get(m.catalog_id) or {}).get("image")
        for row in rows:
            key = (row.model_name, row.audio_sha256, ph)
            if key in seen:
                continue  # one output per model, file and params: the newest run's, newest row first
            seen.add(key)
            o = row.output or {}
            out.append({"model": row.model_name, "image": row.image or run_image, "build_key": row.build_key,
                        "sha256": row.audio_sha256, "params": params, "latency_s": round(float(row.latency_s), 4),
                        "output": {"segments": o.get("segments") or [], "speakers": o.get("speakers")}})
    return out


def preview(session: Session, ds: Dataset, scope: str = "complete") -> dict[str, Any]:
    sel = select_files(session, ds, scope)
    return {"scope": scope, "files": len(sel.files), "segments": sel.segments, "hours": sel.seconds / 3600,
            "left_out": sel.left_out, "label_types": sel.label_types, "refs_file": sel.refs_file,
            "problem": sel.problem, "review": sel.stats, "next_version": next_version(session, ds),
            "models": label_models(session, ds),
            "labels": sorted({k for f in (attributes(sel).get("files") or {}).values() for k in f})}


# ---------------------------------------------------------------- writing

def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40] or "dataset"


def package_name(name: str, version: int) -> str:
    return f"{slug(name)}-refs-v{version}"


def _t(x: float) -> float:
    return round(float(x), 3)


def _rttm_token(s: str) -> str:
    return re.sub(r"\s+", "_", s.strip()) or "spk"


def refs_text(sel: Selection) -> str:
    rows = [(f, s) for f, ss in sel.files for s in ss]
    if sel.refs_file == REFS_SEGLST:
        return json.dumps([{"session_id": f.stem, "speaker": s.speaker, "start_time": _t(s.start),
                            "end_time": _t(s.end), "words": s.text or ""} for f, s in rows], indent=1,
                          ensure_ascii=False)
    if sel.refs_file == REFS_JSONL:
        return "".join(json.dumps({"file": f.rel_path, "start": _t(s.start), "end": _t(s.end), "text": s.text or ""},
                                  ensure_ascii=False) + "\n" for f, s in rows)
    if sel.refs_file == REFS_RTTM:
        return "".join(f"SPEAKER {f.stem} 1 {s.start:.3f} {s.end - s.start:.3f} <NA> <NA> {_rttm_token(s.speaker or '')} "
                       "<NA> <NA>\n" for f, s in rows)
    raise RefPkgError(sel.problem or "nothing to write")


def attributes(sel: Selection) -> dict[str, Any]:
    """refs/attributes.json (CONTRACTS.md §2.7): the files' metadata as file labels; empty when no file has
    any. Label keeps no labels per speaker or per segment, so `speakers` and record `attributes` stay out."""
    files = {f.rel_path: dict(f.meta) for f, _ in sel.files if f.meta}
    return {"files": files} if files else {}


def package_files(session: Session, ds: Dataset, sel: Selection, *, name: str, tags: list[str],
                  version: int | None, audio_included: str) -> tuple[dict[str, Any], dict[str, str]]:
    """The manifest, and the text of manifest.json, files.csv and refs/ by their path in the package. Without
    `version` the manifest has no `source` and `export_version`, which the Dataset Catalog fills in."""
    if sel.problem:
        raise RefPkgError(sel.problem)
    files = [f for f, _ in sel.files]
    manifest: dict[str, Any] = {
        "format": FORMAT, "format_version": FORMAT_VERSION, "name": name,
        "created_at": utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "language": ds.language, "description": ds.description, "tags": list(tags),
        "source": {"app": APP, "install_id": install_id(), "dataset_uid": ds.uid, "dataset_name": ds.name}
        if version is not None else None,
        "export_version": version, "scope": sel.scope, "label_types": sel.label_types,
        "counts": {"files": len(files), "segments": sel.segments, "hours": round(sel.seconds / 3600, 6)},
        "audio_included": audio_included,
        "review": {**sel.stats, "files_left_out": sel.left_out},
        "models": label_models(session, ds),
        "normaliser": normalisers.DEFAULT,
    }
    manifest = {k: v for k, v in manifest.items() if v is not None}
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["path", "sha256", "duration", "sample_rate"])
    for f in files:
        w.writerow([f.rel_path, f.sha256, "" if f.duration is None else round(f.duration, 3), f.orig_sr or ""])
    uids = "".join(json.dumps({"file": f.rel_path, "start": _t(s.start), "end": _t(s.end), "uid": s.uid}) + "\n"
                   for f, ss in sel.files for s in ss)
    out = {"manifest.json": json.dumps(manifest, indent=1, ensure_ascii=False), "files.csv": buf.getvalue(),
           f"refs/{sel.refs_file}": refs_text(sel), "refs/uids.jsonl": uids}
    labels = attributes(sel)
    if labels:
        out["refs/attributes.json"] = json.dumps(labels, indent=1, ensure_ascii=False)
    return manifest, out


def write_package(session: Session, ds: Dataset, sel: Selection, out: Path, *, name: str, version: int,
                  tags: list[str], audio_shas: set[str] | None, include_outputs: bool,
                  progress: Progress = lambda p, m: None) -> dict[str, Any]:
    """Write the zip; `audio_shas` names the files whose audio goes in (None: all). Returns the manifest
    and the counts."""
    import zipfile

    if sel.problem:
        raise RefPkgError(sel.problem)
    root = package_name(name, version)
    files = [f for f, _ in sel.files]
    with_audio = [f for f in files if audio_shas is None or f.sha256 in audio_shas]
    for f in with_audio:
        if not paths.original(f.sha256, f.ext).is_file():
            raise RefPkgError(f"the original audio of {f.rel_path} is missing from this install's data folder")
    outputs = seg_outputs(session, ds, sorted({f.sha256 for f in files})) if include_outputs else []
    n_audio = len(with_audio)
    manifest, parts = package_files(session, ds, sel, name=name, tags=tags, version=version,
                                    audio_included="all" if n_audio == len(files) else (
                                        "none" if n_audio == 0 else "partial"))
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".part")
    total = sum((f.duration or 0.0) for f in with_audio) or 1.0
    done = 0.0
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        for rel, text in parts.items():
            z.writestr(f"{root}/{rel}", text)
        if outputs:
            z.writestr(f"{root}/outputs/seg.jsonl", "".join(json.dumps(o) + "\n" for o in outputs))
        for f in with_audio:
            z.write(paths.original(f.sha256, f.ext), f"{root}/audio/{f.rel_path}", compress_type=zipfile.ZIP_STORED)
            done += f.duration or 0.0
            progress(0.1 + 0.4 * done / total, f"audio: {f.rel_path}")
    tmp.replace(out)
    counts = {**manifest["counts"], "audio_files": n_audio, "outputs": len(outputs), "left_out": sel.left_out,
              "labelled_files": len(attributes(sel).get("files", {})),
              "bytes": out.stat().st_size}
    return {"manifest": manifest, "counts": counts}


def write_version(session: Session, ds: Dataset, sel: Selection, folder: Path, *, name: str,
                  tags: list[str]) -> dict[str, Any]:
    """A Dataset Catalog version (the platform's contracts/datasets/version.md): the package's manifest.json,
    files.csv and refs/ in `folder`, without audio/ and outputs/ (DATASET_CATALOG_PLAN.md §5.1, §8.2). Returns
    the manifest."""
    manifest, parts = package_files(session, ds, sel, name=name, tags=tags, version=None, audio_included="none")
    for rel, text in parts.items():
        (folder / rel).parent.mkdir(parents=True, exist_ok=True)
        (folder / rel).write_text(text, encoding="utf-8")
    return manifest


# ---------------------------------------------------------------- pushing to Bench (CONTRACTS.md §4)

def _bench_call(fn):
    try:
        return fn()
    except httpx.HTTPError as exc:
        peer.mark_unreachable()
        raise peer.PeerError(f"Concord Bench at {peer.bench_url()} did not answer: {type(exc).__name__}: {exc}") \
            from None


def missing_audio(shas: list[str]) -> set[str]:
    """The sha256s Bench has no original for."""
    def call():
        with peer.client(peer.PUSH_TIMEOUT_S) as c:
            return c.post("/api/fingerprints/missing", json={"sha256": shas})
    r = _bench_call(call)
    if r.status_code != 200:
        raise peer.PeerError(f"Concord Bench refused the fingerprint check: {peer.detail(r)}")
    return set(r.json().get("missing") or [])


def push(zip_path: Path, log: Log = print, progress: Progress = lambda p, m: None) -> dict[str, Any]:
    """Upload the zip in chunks (resuming at the offset Bench expects after a 409) and stage it as a
    reference import. Returns Bench's answer: {"id", "status", "preview"}."""
    size = zip_path.stat().st_size

    def call():
        with peer.client(peer.PUSH_TIMEOUT_S) as c:
            r = c.post("/api/uploads")
            if r.status_code not in (200, 201):
                raise peer.PeerError(f"Concord Bench refused the upload: {peer.detail(r)}")
            upload_id = r.json()["upload_id"]
            log(f"uploading {zip_path.name} ({size / 1e6:.1f} MB) as upload {upload_id}")
            off = resumes = 0
            with open(zip_path, "rb") as fh:
                while off < size:
                    fh.seek(off)
                    chunk = fh.read(CHUNK_BYTES)
                    r = c.put(f"/api/uploads/{upload_id}/chunk", params={"path": zip_path.name, "offset": off},
                              content=chunk)
                    if r.status_code == 409:
                        m = re.search(r"expected offset (\d+)", peer.detail(r))
                        if not m or resumes >= MAX_RESUMES:
                            raise peer.PeerError(f"upload out of step at byte {off}: {peer.detail(r)}")
                        resumes += 1
                        off = int(m.group(1))
                        log(f"resuming the upload at byte {off}")
                        continue
                    if r.status_code != 200:
                        raise peer.PeerError(f"upload failed at byte {off}: {peer.detail(r)}")
                    off = int(r.json().get("size", off + len(chunk)))
                    progress(0.5 + 0.45 * off / size, f"uploaded {off / 1e6:.1f} of {size / 1e6:.1f} MB")
            r = c.post("/api/reference-imports", json={"upload_id": upload_id})
            if r.status_code not in (200, 201):
                raise peer.PeerError(f"Concord Bench refused the package: {peer.detail(r)}")
            return r.json()

    return _bench_call(call)


# ---------------------------------------------------------------- the job

def run(session: Session, rec: ReferenceExport, log: Log = print, progress: Progress = lambda p, m: None) -> None:
    """Write the package of `rec` and, for a push, send it to Bench. Fills in `rec`; raises on failure."""
    ds = session.get(Dataset, rec.dataset_id)
    if ds is None:
        raise RefPkgError("the dataset no longer exists")
    if rec.destination == "push":
        st = peer.status(force=True)
        why = peer.refusal(st)
        if why:
            raise peer.PeerError(f"not pushed: {why}")
        rec.bench_url = st["url"]
    sel = select_files(session, ds, rec.scope)
    if sel.problem:
        raise RefPkgError(sel.problem)
    version = next_version(session, ds)
    shas = sorted({f.sha256 for f, _ in sel.files})
    audio: set[str] | None
    if rec.include_audio == "never":
        audio = set()
    elif rec.include_audio == "auto" and rec.destination == "push":
        audio = missing_audio(shas)
        log(f"Concord Bench already has {len(shas) - len(audio)} of {len(shas)} files; leaving their audio out")
    else:
        audio = None
    progress(0.1, "writing the package")
    out = paths.reference_packages() / str(rec.id) / f"{package_name(rec.name, version)}.zip"
    res = write_package(session, ds, sel, out, name=rec.name, version=version, tags=rec.tags, audio_shas=audio,
                        include_outputs=rec.include_outputs, progress=progress)
    rec.version, rec.path, rec.manifest, rec.counts = version, str(out), res["manifest"], res["counts"]
    c = res["counts"]
    log(f"wrote {out.name}: v{version}, {c['files']} files, {c['segments']} segments, {c['audio_files']} with audio, "
        f"{c['outputs']} seg outputs, labels on {c['labelled_files']} files, {c['bytes'] / 1e6:.1f} MB")
    if rec.destination == "push":
        ans = push(out, log, progress)
        rec.bench_import_id = ans.get("id")
        rec.bench_preview = ans.get("preview") or {}
        if isinstance(rec.bench_preview.get("dataset_id"), int):
            rec.bench_dataset_id = rec.bench_preview["dataset_id"]
        log(f"staged in Concord Bench as reference import {rec.bench_import_id} ({ans.get('status')})")
    rec.status, rec.error, rec.finished_at = "done", None, utcnow()
    progress(1.0, "done")


def export_json(rec: ReferenceExport) -> dict[str, Any]:
    m = rec.manifest or {}
    return {"id": rec.id, "dataset_id": rec.dataset_id, "version": rec.version, "name": rec.name, "scope": rec.scope,
            "tags": rec.tags, "include_audio": rec.include_audio, "include_outputs": rec.include_outputs,
            "destination": rec.destination, "status": rec.status, "counts": rec.counts,
            "label_types": m.get("label_types"), "audio_included": m.get("audio_included"),
            "file": Path(rec.path).name if rec.path else None,
            "download": f"/api/reference-exports/{rec.id}/download" if rec.path and rec.status == "done" else None,
            "bench_url": rec.bench_url, "bench_import_id": rec.bench_import_id,
            "bench_dataset_id": rec.bench_dataset_id, "bench_preview": rec.bench_preview, "error": rec.error,
            "catalog_dataset_id": rec.catalog_dataset_id,
            "catalog_url": dataset_catalog.ui_url(rec.catalog_dataset_id, rec.version if rec.status == "done" else None)
            if rec.catalog_dataset_id else None,
            "job_id": rec.job_id, "created_at": rec.created_at.isoformat(),
            "finished_at": rec.finished_at.isoformat() if rec.finished_at else None}
