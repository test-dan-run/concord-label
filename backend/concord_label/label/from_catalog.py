"""New from Dataset Catalog (DATASET_CATALOG_PLAN.md §8.3): a working dataset made from a published version.

The version's folder and audio are read from the store's mount (the platform's contracts/datasets/version.md):

- each file in files.csv points at its audio in the store, which Label holds, and gets its derived FLAC and peaks
  in data/ as at ingest;
- the references file becomes confirmed segments, matched to the files by stem as at ingest, and each segment
  takes its uid from refs/uids.jsonl when the file and times match within 0.01 s, so flags still find it;
- the file labels in refs/attributes.json go into the files' `meta`, as do the record fields that are the same on
  every segment of a file (as at ingest);
- a version with audio only makes an `audio_only` dataset.

The dataset keeps `catalog_dataset_id` and `catalog_version`, so its next publish is based on this version. The
catalog records the dataset as a use of the version (ref: the dataset's uid), and `report.catalog` says where it
came from.
"""

from __future__ import annotations

import csv
import json
from collections import Counter
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from typing import Any

from sqlalchemy.orm import Session

from ..core.models import AudioFile, Dataset, FileStatus, Segment, SegmentStatus
from ..datasets.ingest import _process_audio, label_types_for
from ..datasets.parsers import RefRecord, flatten, parse_file, stem_of
from ..platform import datasets as dataset_catalog

Progress = Callable[[float, str], None]
FORMAT = "concord-reference-package"
FORMAT_VERSIONS = (1, 2, 3)
SIDE_FILES = ("uids.jsonl", "attributes.json")
UID_TOLERANCE_S = 0.01


class VersionError(ValueError):
    pass


def read_version(root: Path) -> tuple[dict[str, Any], list[dict[str, str]], Path | None]:
    """The manifest, the rows of files.csv and the references file (None for a version with audio only)."""
    if not (root / "manifest.json").is_file():
        raise VersionError(f"no version at {root}: is DATASET_STORE the Dataset Catalog's store?")
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("format") != FORMAT or manifest.get("format_version") not in FORMAT_VERSIONS:
        raise VersionError(f"{root}: format {manifest.get('format')!r} version {manifest.get('format_version')!r}; "
                           f"Concord Label reads {FORMAT} versions {', '.join(map(str, FORMAT_VERSIONS))}")
    with open(root / "files.csv", newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    side = ("uids.jsonl",) if manifest["format_version"] == 1 else SIDE_FILES
    refs = sorted(p for p in (root / "refs").iterdir() if p.is_file() and p.name not in side) \
        if (root / "refs").is_dir() else []
    if manifest.get("label_types") and len(refs) != 1:
        raise VersionError(f"{root}: refs/ must hold one references file, found {len(refs)}")
    return manifest, rows, refs[0] if refs else None


def _uids(root: Path) -> dict[str, list[tuple[float, float, str]]]:
    p = root / "refs" / "uids.jsonl"
    out: dict[str, list[tuple[float, float, str]]] = {}
    if p.is_file():
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                out.setdefault(r["file"], []).append((float(r["start"]), float(r["end"]), str(r["uid"])))
    return out


def _take_uid(candidates: list[tuple[float, float, str]], start: float, end: float) -> str | None:
    for k, (s, e, uid) in enumerate(candidates):
        if abs(s - start) <= UID_TOLERANCE_S and abs(e - end) <= UID_TOLERANCE_S:
            del candidates[k]
            return uid
    return None


def ingest_version(session: Session, dataset_id: int, progress: Progress | None = None) -> dict[str, Any]:
    """Fill the dataset `dataset_id` (status ingesting, with catalog_dataset_id and catalog_version set) from its
    catalog version. Returns the report, like datasets.ingest.ingest."""
    progress = progress or (lambda p, m: None)
    ds = session.get(Dataset, dataset_id)
    if ds is None or not ds.catalog_dataset_id or not ds.catalog_version:
        raise ValueError(f"dataset {dataset_id} names no catalog version")
    dc = dataset_catalog.get()
    did, n = ds.catalog_dataset_id, ds.catalog_version
    with dataset_catalog.calls(f"reading v{n} of {did}"):
        version = dc.version(did, n)
        root = dc.version_dir(did, n)
        manifest, rows, refs_path = read_version(root)
        lost = [r["path"] for r in rows if not dc.audio_path(r["sha256"]).is_file()]
        if lost:
            raise VersionError(f"{len(lost)} files are not in the store at {dc.store}: {', '.join(lost[:5])}")
        progress(0.02, "holding the audio")
        dc.hold(sorted({r["sha256"] for r in rows}))

    parse_errors: list[dict[str, Any]] = []
    records: list[RefRecord] = []
    if refs_path is not None:
        res = parse_file(refs_path, f"refs/{refs_path.name}")
        records = res.records
        if res.errors:
            parse_errors.append({"file": res.path, "format": res.format, "errors": res.errors[:20],
                                 "count": len(res.errors)})
    by_stem: dict[str, list[RefRecord]] = {}
    for rec in records:
        by_stem.setdefault(rec.stem.lower(), []).append(rec)
    known = {stem_of(r["path"]).lower() for r in rows}
    unmatched = sorted({rec.stem for rec in records if rec.stem.lower() not in known})
    uids = _uids(root)
    attrs_path = root / "refs" / "attributes.json"
    attrs = json.loads(attrs_path.read_text(encoding="utf-8")) if attrs_path.is_file() else {}
    file_labels = attrs.get("files") or {}

    flagged: dict[str, list[dict[str, Any]]] = {"clipped": [], "undecodable": []}
    sr_hist: Counter[int] = Counter()
    seconds, n_segments, with_uid = 0.0, 0, 0
    for k, row in enumerate(rows):
        rel = row["path"]
        progress(0.05 + 0.9 * k / max(len(rows), 1), f"audio {k + 1}/{len(rows)}: {rel}")
        f = AudioFile(dataset_id=ds.id, rel_path=rel, sha256=row["sha256"], ext=PurePosixPath(rel).suffix.lower())
        facts = _process_audio(f, dc.audio_path(row["sha256"]))  # paths.original finds the store's copy
        session.add(f)
        session.flush()
        if f.status != FileStatus.ok:
            item = {"file": rel, **({"error": facts["error"]} if "error" in facts else {})}
            if f.status == FileStatus.clipped:
                item["clipped_pct"] = round(100 * facts["clipped_fraction"], 3)
            flagged[f.status].append(item)
        if f.orig_sr:
            sr_hist[f.orig_sr] += 1
        seconds += f.duration or 0.0
        recs = by_stem.get(stem_of(rel).lower(), [])
        mine = uids.get(rel, [])
        for rec in recs:
            start, end = (0.0, f.duration or 0.0) if rec.start is None else (rec.start, rec.end)
            seg = Segment(audio_file_id=f.id, start=start, end=end, speaker=rec.speaker, text=rec.text,
                          status=SegmentStatus.confirmed)
            uid = _take_uid(mine, start, end)
            if uid:
                seg.uid = uid
                with_uid += 1
            session.add(seg)
        n_segments += len(recs)
        meta = {k: v for k, v in recs[0].meta.items() if all(r.meta.get(k) == v for r in recs[1:])} if recs else {}
        labels = file_labels.get(rel)
        meta.update(flatten(labels) if isinstance(labels, dict) else {})
        if meta:
            f.meta = meta
        session.commit()  # short write transactions: the API shares this SQLite file

    progress(0.97, "writing report")
    with dataset_catalog.calls("recording the use of the version"):
        dc.add_use(did, n, ds.uid)
    ds.label_types = label_types_for(records)
    ds.mode = "annotated" if ds.label_types else "audio_only"
    files = list(ds.files)
    usable = [f for f in files if f.usable]
    with_refs = {stem_of(f.rel_path).lower() for f in files} & set(by_stem)
    without = sorted(f.rel_path for f in files if stem_of(f.rel_path).lower() not in with_refs)
    kinds = Counter(rec.kind for rec in records)
    ds.report = {
        "files": len(files), "usable_files": len(usable), "hours": seconds / 3600,
        "usable_hours": sum(f.duration or 0 for f in usable) / 3600,
        "sample_rates": {str(k): v for k, v in sorted(sr_hist.items())},
        "flagged": flagged,
        "tasks": {kind: {"records": c, "files": len({r.stem.lower() for r in records if r.kind == kind} & known),
                         "formats": [refs_path.suffix.lstrip(".")] if refs_path else [], "preview": []}
                  for kind, c in kinds.items()},
        "unmatched": unmatched,
        "audio_without_labels": without[:200] if records else [],
        "audio_without_labels_count": len(without) if records else 0,
        "duplicate_stems": [], "parse_errors": parse_errors,
        "files_by_format": [{"file": f"refs/{refs_path.name}", "format": refs_path.suffix.lstrip("."),
                             "records": len(records), "tasks": sorted(kinds)}] if refs_path else [],
        "has_speaker_text": "spk_asr" in kinds,
        "segments": n_segments, "segments_with_uid": with_uid,
        "catalog": {"dataset_id": did, "version": n, "name": manifest.get("name"),
                    "published_by": version.get("published_by"), "published_at": version.get("published_at"),
                    "format_version": manifest.get("format_version")},
    }
    ds.status = "ready"
    session.flush()
    progress(1.0, "done")
    return ds.report
