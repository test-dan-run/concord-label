"""Annotate's undo and redo: a history of the edits a person makes to each file's segments.

Every edit in `edit.py` (and the delete route) takes a `snapshot` of the segments it touches before and after,
and `record`s both in the same transaction. `undo` deletes the segments an edit left and writes back the ones
it found, with their old `id` and `uid`; `redo` does the same the other way round. A restored segment gets a
new `updated_at`, so a label package of the changes since the last export still carries it.

Only a person's edits are recorded. A pseudo-label run or a package import changes segments without a record,
so it calls `forget` for the files it touches. Before an undo or a redo, each segment it would remove must
still be as the edit left it, and each segment it would write back must still be gone; otherwise the history
no longer matches the file, and `HistoryError` says so after the history is forgotten.
"""

from __future__ import annotations

import copy
from collections.abc import Iterable
from datetime import datetime
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..core.models import AudioFile, Run, Segment, SegmentEdit, utcnow

KEEP = 200  # edits kept for each file; older ones are deleted
COLUMNS = ("id", "uid", "audio_file_id", "start", "end", "speaker", "text", "status", "confidence", "source_run_id",
           "flagged", "flag_note", "edited_at", "meta", "updated_at")
CHANGED = "This file changed since that edit, so it can't be undone"


class HistoryError(ValueError):
    pass


def snapshot(segments: Iterable[Segment]) -> list[dict[str, Any]]:
    """Every column of each segment, as JSON. Call it after a flush, so new segments have their ids."""
    out = []
    for s in segments:
        row = {c: getattr(s, c) for c in COLUMNS}
        for c in ("edited_at", "updated_at"):
            row[c] = row[c].isoformat() if row[c] else None
        row["meta"] = copy.deepcopy(row["meta"] or {})
        out.append(row)
    return out


def record(session: Session, f: AudioFile | int, action: str, before: list[dict[str, Any]],
           after: list[dict[str, Any]]) -> SegmentEdit | None:
    """Save an edit to file `f`. It drops the file's undone edits (redo only works straight after an undo)
    and all but the newest KEEP edits. An edit that changed nothing is not saved."""
    if _same(before, after):
        return None
    fid = f if isinstance(f, int) else f.id
    session.execute(delete(SegmentEdit).where(SegmentEdit.audio_file_id == fid, SegmentEdit.undone.is_(True)))
    e = SegmentEdit(audio_file_id=fid, action=action, before=before, after=after, undone=False)
    session.add(e)
    session.flush()
    keep = select(SegmentEdit.id).where(SegmentEdit.audio_file_id == fid).order_by(SegmentEdit.id.desc()).limit(KEEP)
    session.execute(delete(SegmentEdit).where(SegmentEdit.audio_file_id == fid, SegmentEdit.id.not_in(keep)))
    return e


def forget(session: Session, file_ids: Iterable[int]) -> None:
    """Delete the history of these files, after something other than a person changed their segments."""
    ids = list(file_ids)
    if ids:
        session.execute(delete(SegmentEdit).where(SegmentEdit.audio_file_id.in_(ids)))


def state(session: Session, file_id: int) -> dict[str, str | None]:
    """The action names of the next undo and the next redo (None when there is nothing to undo or redo)."""
    u = _next(session, file_id, undone=False)
    r = _next(session, file_id, undone=True)
    return {"undo": u.action if u else None, "redo": r.action if r else None}


def undo(session: Session, file_id: int) -> dict[str, Any]:
    """Undo the file's newest edit that is not undone. Returns its action and the ids of the restored
    segments."""
    e = _next(session, file_id, undone=False)
    if e is None:
        raise HistoryError("There is nothing to undo")
    _swap(session, e, remove=e.after, restore=e.before)
    e.undone = True
    session.flush()
    return {"action": e.action, "ids": [r["id"] for r in e.before]}


def redo(session: Session, file_id: int) -> dict[str, Any]:
    """Redo the file's oldest undone edit, which is the one undone last."""
    e = _next(session, file_id, undone=True)
    if e is None:
        raise HistoryError("There is nothing to redo")
    _swap(session, e, remove=e.before, restore=e.after)
    e.undone = False
    session.flush()
    return {"action": e.action, "ids": [r["id"] for r in e.after]}


def _next(session: Session, file_id: int, undone: bool) -> SegmentEdit | None:
    order = SegmentEdit.id.asc() if undone else SegmentEdit.id.desc()
    return session.scalar(select(SegmentEdit).where(SegmentEdit.audio_file_id == file_id,
                                                    SegmentEdit.undone.is_(undone)).order_by(order).limit(1))


def _content(row: dict[str, Any]) -> dict[str, Any]:
    # updated_at changes whenever a segment is written back, and edited_at whenever a person touches it
    # again, so these compare whether a person has touched it at all
    return {**{k: v for k, v in row.items() if k != "updated_at"}, "edited_at": bool(row["edited_at"])}


def _same(a: list[dict[str, Any]], b: list[dict[str, Any]]) -> bool:
    return sorted(map(repr, map(_content, a))) == sorted(map(repr, map(_content, b)))


def _swap(session: Session, e: SegmentEdit, remove: list[dict[str, Any]], restore: list[dict[str, Any]]) -> None:
    file_id = e.audio_file_id
    gone = {r["id"] for r in remove}
    segs = {s.id: s for s in session.scalars(select(Segment).where(Segment.id.in_(gone | {r["id"] for r in restore})))}
    ok = all(r["id"] in segs and _content(snapshot([segs[r["id"]]])[0]) == _content(r) for r in remove) \
        and all(r["id"] not in segs or r["id"] in gone for r in restore)
    if not ok:
        forget(session, [file_id])
        session.flush()
        raise HistoryError(CHANGED)
    for i in gone:
        session.delete(segs[i])
    session.flush()
    for r in restore:
        session.add(_segment(session, r))
    session.flush()


def _segment(session: Session, row: dict[str, Any]) -> Segment:
    s = Segment(**{k: v for k, v in row.items() if k not in ("edited_at", "updated_at")})
    s.edited_at = _time(row["edited_at"])
    s.updated_at = utcnow()
    if s.source_run_id is not None and session.get(Run, s.source_run_id) is None:
        s.source_run_id = None  # the run was deleted since
    return s


def _time(v: str | None) -> datetime | None:
    return datetime.fromisoformat(v) if v else None
