"""Flags back from Concord Bench (CONTRACTS.md §3, SPLIT_PLAN.md §4.5).

Bench's "Flag reference as wrong" comes back as a flags file. The dataset is the one whose uid is the
file's `source.dataset_uid`, and only when `source.install_id` is this install's. Each flag is matched
by segment uid, else by audio sha256 and time overlap (IoU >= 0.5) within the dataset. Matched segments
get `flagged` and the note, and show in the annotator's flags lane. A flag is not a review: the segment's
status and `edited_at` stay as they are.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.models import AudioFile, Dataset, Segment, SegmentStatus
from .sync import install_id

FORMAT = "concord-flags"
FORMAT_VERSION = 1
IOU_MATCH = 0.5


class FlagsError(ValueError):
    """The flags file is not one this install can read (HTTP 400)."""


class UnknownSource(LookupError):
    """No dataset here is the flags file's source (HTTP 404)."""


def _iou(a0: float, a1: float, b0: float, b1: float) -> float:
    inter = max(0.0, min(a1, b1) - max(a0, b0))
    union = max(a1, b1) - min(a0, b0)
    return inter / union if union > 0 else 0.0


def find_dataset(session: Session, source: dict[str, Any]) -> Dataset:
    uid, inst = source.get("dataset_uid"), source.get("install_id")
    if inst and inst != install_id():
        raise UnknownSource(f"these flags are for install {inst}; this Concord Label is install {install_id()}")
    ds = session.scalar(select(Dataset).where(Dataset.uid == uid)) if uid else None
    if ds is None:
        name = source.get("dataset_name")
        raise UnknownSource(f"no dataset here has uid {uid!r}" + (f" ({name!r})" if name else "")
                            + ": the flags must come from a package this install sent")
    return ds


def import_flags(session: Session, body: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(body, dict) or body.get("format") != FORMAT:
        raise FlagsError(f"not a flags file (format {body.get('format') if isinstance(body, dict) else None!r}; "
                         f"expected {FORMAT!r})")
    if body.get("format_version") != FORMAT_VERSION:
        raise FlagsError(f"flags format version {body.get('format_version')!r} is not known here "
                         f"(known: {FORMAT_VERSION})")
    flags = body.get("flags")
    if not isinstance(flags, list):
        raise FlagsError("flags must be a list")
    ds = find_dataset(session, body.get("source") or {})
    files = list(session.scalars(select(AudioFile).where(AudioFile.dataset_id == ds.id)))
    by_sha: dict[str, list[AudioFile]] = {}
    for f in files:
        by_sha.setdefault(f.sha256, []).append(f)
    uids = [f.get("uid") for f in flags if isinstance(f, dict) and f.get("uid")]
    by_uid = {s.uid: s for s in session.scalars(select(Segment).join(AudioFile).where(
        AudioFile.dataset_id == ds.id, Segment.uid.in_(uids)))} if uids else {}
    matched: list[dict[str, Any]] = []
    unmatched: list[dict[str, Any]] = []
    for flag in flags:
        if not isinstance(flag, dict):
            raise FlagsError("each flag must be an object")
        seg, how = by_uid.get(flag.get("uid") or ""), "uid"
        if seg is None:
            how = "overlap"
            try:
                t0, t1 = float(flag["start"]), float(flag["end"])
            except (KeyError, TypeError, ValueError):
                t0 = t1 = None
            cands = [s for f in by_sha.get(flag.get("file_sha256") or "", []) for s in f.segments]
            scored = [(_iou(s.start, s.end, t0, t1), s.status == SegmentStatus.confirmed, -s.id, s)
                      for s in cands if t0 is not None]
            scored = [c for c in scored if c[0] >= IOU_MATCH]
            seg = max(scored, key=lambda c: c[:3])[3] if scored else None
        if seg is None:
            unmatched.append(flag)
            continue
        seg.flagged = True
        if flag.get("note"):
            seg.flag_note = str(flag["note"])
        matched.append({"segment_id": seg.id, "file_id": seg.audio_file_id, "by": how})
    session.flush()
    return {"matched": len(matched), "unmatched": len(unmatched), "dataset_id": ds.id, "dataset": ds.name,
            "segments": matched, "unmatched_flags": unmatched}
