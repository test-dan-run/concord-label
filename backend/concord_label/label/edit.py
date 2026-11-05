"""Annotate: segment edits, bulk actions, merge / split, and the per-file view.

Every change made here is a person's, so it sets `edited_at`: pseudo-labelling never replaces the segment
afterwards, and label packages carry it as reviewed work. Merging or splitting drops the members'
outputs from `meta` (they were transcribed on the old span), so live evaluation only uses outputs that
match the segment. Each change is also recorded in its file's history, so Annotate can undo and redo it
(label/history.py).
"""

from __future__ import annotations

from collections import Counter, defaultdict
from math import floor
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..core.models import AudioFile, Dataset, FileStatus, Model, Run, Segment, SegmentStatus, utcnow
from ..runs import cache
from . import console, fusion, history

EDITABLE = ("text", "speaker", "start", "end", "status", "flagged", "flag_note", "spelling_kept")
MIN_LEN = 0.05
TOUCH = 0.02  # two segments touch when one ends within this many seconds of where the other starts
USABLE = (FileStatus.ok, FileStatus.clipped)


class EditError(ValueError):
    pass


def _uploaded_differs(text: str | None, label: str | None) -> bool | None:
    """Whether a transcript says something other than its uploaded label; None with nothing to compare."""
    return None if text is None or label is None else not fusion.same_text(text, label)


def segment_json(s: Segment) -> dict[str, Any]:
    m = s.meta or {}
    label = (m.get("uploaded") or {}).get("text")
    return {"id": s.id, "uid": s.uid, "file_id": s.audio_file_id, "start": s.start, "end": s.end,
            "speaker": s.speaker, "text": s.text, "status": s.status, "confidence": s.confidence,
            "source_run_id": s.source_run_id, "flagged": s.flagged, "flag_note": s.flag_note,
            "reviewed": s.reviewed, "edited_at": s.edited_at.isoformat() if s.edited_at else None,
            "updated_at": s.updated_at.isoformat() if s.updated_at else None,
            "chosen": m.get("chosen"), "agreement": m.get("agreement"), "words": m.get("words"),
            "created": bool(m.get("created")), "spelling_kept": m.get("spelling_kept") or [],
            "uploaded_text": label, "uploaded_differs": _uploaded_differs(s.text, label)}


def _touch(s: Segment) -> None:
    s.edited_at = utcnow()


def _record(session: Session, action: str, before: list[dict[str, Any]], after: list[dict[str, Any]]) -> None:
    """Record an edit in the history of each file it touched."""
    was, now = defaultdict(list), defaultdict(list)
    for r in before:
        was[r["audio_file_id"]].append(r)
    for r in after:
        now[r["audio_file_id"]].append(r)
    files = sorted(was.keys() | now.keys())
    if len(files) == 1:
        history.record(session, files[0], action, was[files[0]], now[files[0]])
    else:
        history.record_many(session, action, {fid: (was[fid], now[fid]) for fid in files})


def _update_action(fields: dict[str, Any], before: dict[str, Any], s: Segment) -> str:
    """The name of a PATCH in the history, e.g. "text edit" or "move" (a drag on the speaker lanes)."""
    if "text" in fields:
        return "text edit"
    if "start" in fields or "end" in fields:
        return "move" if s.start != before["start"] and s.end != before["end"] else "trim"
    if "speaker" in fields:
        return "speaker change"
    if "status" in fields:
        return "status change"
    if "flagged" in fields:
        return "flag" if fields["flagged"] else "unflag"
    if "spelling_kept" in fields:
        return "spelling choice"
    return "edit"


def _check_span(f: AudioFile, start: float, end: float) -> None:
    dur = f.duration or 0.0
    if start < 0 or end > dur + 1e-3 or end - start < MIN_LEN:
        raise EditError(f"segment {start:.3f}–{end:.3f} s must lie inside the file (0–{dur:.3f} s) and last "
                        f"at least {MIN_LEN} s")


def update(session: Session, s: Segment, fields: dict[str, Any]) -> Segment:
    bad = set(fields) - set(EDITABLE)
    if bad:
        raise EditError(f"cannot edit {', '.join(sorted(bad))}")
    if "status" in fields and fields["status"] not in tuple(SegmentStatus):
        raise EditError(f"unknown status {fields['status']!r}")
    before = history.snapshot([s])
    start, end = fields.get("start", s.start), fields.get("end", s.end)
    if "start" in fields or "end" in fields:
        _check_span(s.audio_file, float(start), float(end))
        if (round(start, 3), round(end, 3)) != (s.start, s.end):
            s.meta = {k: v for k, v in (s.meta or {}).items() if k not in ("members", "span", "words")}
    for k, v in fields.items():
        if k == "spelling_kept":
            # spelling hints the annotator chose to keep (label/lexicon.py); lower case, single spaces
            kept = sorted({" ".join(str(x).lower().split()) for x in v or []} - {""})
            s.meta = {**(s.meta or {}), "spelling_kept": kept}
            continue
        if k == "speaker":
            v = (v or "").strip() or None
        if k in ("start", "end"):
            v = round(float(v), 3)
        setattr(s, k, v)
    _touch(s)
    session.flush()
    _record(session, _update_action(fields, before[0], s), before, history.snapshot([s]))
    return s


BULK_ACTIONS = {"confirm": "confirm", "reject": "reject", "review": "review", "assign_speaker": "speaker change",
                "rename_speaker": "speaker rename", "flag": "flag", "unflag": "unflag"}


def bulk(session: Session, segs: list[Segment], action: str, speaker: str | None = None,
         history_name: str | None = None) -> int:
    """`action` on every segment; `history_name` names the edit in the history (by default from BULK_ACTIONS)."""
    if action not in BULK_ACTIONS:
        raise EditError(f"unknown action {action!r}")
    before = history.snapshot(segs)
    if action == "confirm":
        for s in segs:
            s.status = SegmentStatus.confirmed
    elif action == "reject":
        for s in segs:
            s.status = SegmentStatus.rejected
    elif action == "review":
        for s in segs:
            s.status = SegmentStatus.pseudo
    elif action in ("assign_speaker", "rename_speaker"):
        # rename_speaker is assign_speaker on every segment of one speaker (Annotate's lane names)
        name = (speaker or "").strip()
        if not name:
            raise EditError(f"{action} needs a speaker")
        for s in segs:
            s.speaker = name
    elif action == "flag":
        for s in segs:
            s.flagged = True
    elif action == "unflag":
        for s in segs:
            s.flagged = False
    for s in segs:
        _touch(s)
    session.flush()
    _record(session, history_name or BULK_ACTIONS[action], before, history.snapshot(segs))
    return len(segs)


def merge(session: Session, segs: list[Segment]) -> Segment:
    if len(segs) < 2:
        raise EditError("merge needs at least two segments")
    if len({s.audio_file_id for s in segs}) != 1:
        raise EditError("only segments of the same file can be merged")
    segs = sorted(segs, key=lambda s: (s.start, s.end))
    before = history.snapshot(segs)
    keep = segs[0]
    by_spk: dict[str | None, float] = {}
    for s in segs:
        by_spk[s.speaker] = by_spk.get(s.speaker, 0.0) + (s.end - s.start)
    texts = [s.text.strip() for s in segs if s.text and s.text.strip()]
    words = [w for s in segs for w in (s.meta or {}).get("words") or []] \
        if all((s.meta or {}).get("words") for s in segs if s.text) else None
    confs = [s.confidence for s in segs if s.confidence is not None]
    keep.end = max(s.end for s in segs)
    keep.speaker = max(by_spk, key=lambda k: (by_spk[k], k is not None))
    keep.text = " ".join(texts) if texts else (None if all(s.text is None for s in segs) else "")
    keep.status = SegmentStatus.confirmed if all(s.status == SegmentStatus.confirmed for s in segs) \
        else SegmentStatus.pseudo
    keep.confidence = min(confs) if confs else None
    keep.flagged = any(s.flagged for s in segs)
    keep.meta = {"merged_from": [s.uid for s in segs], **({"words": words} if words else {})}
    for s in segs[1:]:
        session.delete(s)
    _touch(keep)
    session.flush()
    _record(session, "merge", before, history.snapshot([keep]))
    return keep


def boundary(session: Session, segs: list[Segment], t: float) -> list[Segment]:
    """Move the edge where these segments touch to `t`: the ends of the ones that end there and the starts of
    the ones that start there, so a change of speaker stays in the same place on every lane. One edit."""
    if len(segs) < 2:
        raise EditError("a shared boundary needs at least two segments")
    if len({s.audio_file_id for s in segs}) != 1:
        raise EditError("only segments of the same file share a boundary")
    ends = [s.end for s in segs if any(abs(s.end - o.start) <= TOUCH for o in segs if o is not s)]
    if not ends:
        raise EditError("these segments don't touch")
    x = sum(ends) / len(ends)
    left = [s for s in segs if abs(s.end - x) <= TOUCH]
    right = [s for s in segs if abs(s.start - x) <= TOUCH and s not in left]
    if len(left) + len(right) != len(segs) or not right:
        raise EditError("these segments don't share one boundary")
    t = round(float(t), 3)
    for s in left:
        _check_span(s.audio_file, s.start, t)
    for s in right:
        _check_span(s.audio_file, t, s.end)
    before = history.snapshot(segs)
    for s in left:
        s.end = t
    for s in right:
        s.start = t
    for s in segs:
        s.meta = {k: v for k, v in (s.meta or {}).items() if k not in ("members", "span", "words")}
        _touch(s)
    session.flush()
    _record(session, "boundary move", before, history.snapshot(segs))
    return segs


def _split_text(s: Segment, t: float) -> tuple[str | None, str | None, list | None, list | None]:
    if s.text is None:
        return None, None, None, None
    words = (s.meta or {}).get("words")
    if words:
        a = [w for w in words if (w["start"] + w["end"]) / 2 < t]
        b = [w for w in words if (w["start"] + w["end"]) / 2 >= t]
        return " ".join(w["w"] for w in a), " ".join(w["w"] for w in b), a, b
    toks = s.text.split()
    k = round(len(toks) * (t - s.start) / max(1e-9, s.end - s.start))
    return " ".join(toks[:k]), " ".join(toks[k:]), None, None


def split(session: Session, s: Segment, t: float) -> tuple[Segment, Segment]:
    """Split at time t. Text follows the word times when the segment has them, otherwise it is split
    in proportion to time; both halves go back to review."""
    t = round(float(t), 3)
    if t - s.start < MIN_LEN or s.end - t < MIN_LEN:
        raise EditError(f"split point {t:.3f} s must be inside the segment ({s.start:.3f}–{s.end:.3f} s)")
    before = history.snapshot([s])
    ta, tb, wa, wb = _split_text(s, t)
    status = SegmentStatus.rejected if s.status == SegmentStatus.rejected else SegmentStatus.pseudo
    second = Segment(audio_file_id=s.audio_file_id, start=t, end=s.end, speaker=s.speaker, text=tb, status=status,
                     confidence=s.confidence, source_run_id=s.source_run_id, flagged=s.flagged,
                     meta={"split_from": s.uid, **({"words": wb} if wb is not None else {})})
    s.end, s.text, s.status = t, ta, status
    s.meta = {"split_from": s.uid, **({"words": wa} if wa is not None else {})}
    _touch(s)
    _touch(second)
    session.add(second)
    session.flush()
    _record(session, "split", before, history.snapshot([s, second]))
    return s, second


def create(session: Session, f: AudioFile, start: float, end: float, speaker: str | None, text: str | None) -> Segment:
    _check_span(f, start, end)
    s = Segment(audio_file_id=f.id, start=round(start, 3), end=round(end, 3), speaker=(speaker or "").strip() or None,
                text=text, status=SegmentStatus.pseudo, meta={"created": True})
    _touch(s)
    session.add(s)
    session.flush()
    _record(session, "new segment", [], history.snapshot([s]))
    return s


def remove(session: Session, s: Segment) -> None:
    before = history.snapshot([s])
    session.delete(s)
    session.flush()
    _record(session, "delete", before, [])


def confirm_auto(session: Session, f: AudioFile) -> int:
    """'Confirm all high-confidence': every auto-accepted segment of the file becomes confirmed."""
    segs = list(session.scalars(select(Segment).where(Segment.audio_file_id == f.id,
                                                      Segment.status == SegmentStatus.auto)))
    return bulk(session, segs, "confirm", history_name="confirm all high-confidence") if segs else 0


def confirm_auto_dataset(session: Session, ds: Dataset) -> int:
    """'Confirm all high-confidence' for every clip of a dataset that `clip_list` shows as auto-accepted: a usable
    clip with one transcript, so a clip with several segments waits for a merge. Each file's history records its own
    edit."""
    several = (select(Segment.audio_file_id).where(Segment.status != SegmentStatus.rejected)
               .group_by(Segment.audio_file_id).having(func.count(Segment.id) > 1))
    segs = list(session.scalars(select(Segment).join(AudioFile).where(
        AudioFile.dataset_id == ds.id, AudioFile.status.in_(USABLE), Segment.status == SegmentStatus.auto,
        Segment.audio_file_id.not_in(several))))
    return bulk(session, segs, "confirm", history_name="confirm all high-confidence") if segs else 0


def _waiting_uploaded(session: Session, ds: Dataset) -> list[Segment]:
    """The dataset's uploaded labels that nobody has checked, in its usable files."""
    segs = session.scalars(select(Segment).join(AudioFile).where(
        AudioFile.dataset_id == ds.id, AudioFile.status.in_(USABLE), func.json_type(Segment.meta, "$.uploaded").is_not(None),
        Segment.status != SegmentStatus.confirmed, Segment.edited_at.is_(None)))
    return list(segs)


def uploaded_counts(session: Session, ds: Dataset) -> dict[str, int]:
    """How many segments of the dataset hold an uploaded label, and how many of those wait for a person."""
    labels = (select(func.count(Segment.id)).join(AudioFile)
              .where(AudioFile.dataset_id == ds.id, AudioFile.status.in_(USABLE),
                     func.json_type(Segment.meta, "$.uploaded").is_not(None)))
    return {"labels": session.scalar(labels) or 0,
            "waiting": session.scalar(labels.where(Segment.status != SegmentStatus.confirmed,
                                                   Segment.edited_at.is_(None))) or 0}


def confirm_uploaded(session: Session, ds: Dataset) -> int:
    """'Keep all uploaded labels': each waiting segment takes its uploaded label back and becomes confirmed, in place
    of any pseudo-label it holds. Each file's history records its own edit."""
    segs = _waiting_uploaded(session, ds)
    if not segs:
        return 0
    before = history.snapshot(segs)
    for s in segs:
        s.text, s.status = s.uploaded.get("text"), SegmentStatus.confirmed
        _touch(s)
    session.flush()
    _record(session, "keep uploaded label", before, history.snapshot(segs))
    return len(segs)


# ---------------------------------------------------------------- views

def file_counts(session: Session, dataset_id: int) -> list[dict[str, Any]]:
    """Each file with its segments by status. `agreement` counts the segments nobody has checked by their agreement
    in whole percent, rounded as the page rounds it, so the page can say which files hold segments in a range."""
    files = session.scalars(select(AudioFile).where(AudioFile.dataset_id == dataset_id)
                            .order_by(AudioFile.rel_path)).all()
    counts: dict[int, dict[str, int]] = {}
    agreement: dict[int, Counter[int]] = defaultdict(Counter)
    for fid, st, flag, conf, edited in session.execute(
            select(Segment.audio_file_id, Segment.status, Segment.flagged, Segment.confidence, Segment.edited_at)
            .join(AudioFile).where(AudioFile.dataset_id == dataset_id)):
        c = counts.setdefault(fid, {"confirmed": 0, "auto": 0, "review": 0, "rejected": 0, "flagged": 0})
        c[console.STATUS_BAR.get(st, "review")] += 1
        c["flagged"] += bool(flag)
        if conf is not None and edited is None and st != SegmentStatus.confirmed:
            agreement[fid][floor(100 * conf + 0.5)] += 1
    return [{"id": f.id, "path": f.rel_path, "stem": f.stem, "duration": f.duration, "status": f.status,
             "usable": f.usable, "counts": counts.get(f.id, {"confirmed": 0, "auto": 0, "review": 0, "rejected": 0,
                                                             "flagged": 0}),
             "agreement": agreement.get(f.id, {})} for f in files]


def _thresholds(session: Session, dataset_id: int) -> dict[str, float]:
    run = console.latest_label_run(session, dataset_id, done=True)
    th = (run.outputs.get("summary") or {}).get("thresholds") if run else None
    return th or {"high": 0.85, "low": 0.5}


def clip_list(session: Session, ds: Dataset) -> dict[str, Any]:
    """The usable clips of a dataset with their transcripts, in one go, so the page can list thousands of clips and
    walk them without asking again. `segments` counts a clip's segments that aren't rejected, which is 1 in a clip
    that fits this way of labelling. `segment` is the clip's transcript: that one segment, a rejected one when no
    other is left, or the first of several with all their text and the lowest of their confidences. Several segments
    have no one text to compare with an uploaded label. Only the JSON fields the list needs are read."""
    files = session.execute(select(AudioFile.id, AudioFile.rel_path, AudioFile.duration, AudioFile.status)
                            .where(AudioFile.dataset_id == ds.id).order_by(AudioFile.rel_path)).all()
    by_file: dict[int, list[Any]] = defaultdict(list)
    for row in session.execute(select(Segment.audio_file_id, Segment.id, Segment.text, Segment.status,
                                      Segment.confidence, Segment.flagged, Segment.edited_at,
                                      Segment.meta["created"], Segment.meta["spelling_kept"],
                                      Segment.meta["uploaded"]["text"])
                               .join(AudioFile).where(AudioFile.dataset_id == ds.id)
                               .order_by(Segment.start, Segment.end)):
        by_file[row[0]].append(row)
    clips = []
    for fid, path, duration, status in files:
        if status not in USABLE:
            continue
        rows = by_file.get(fid, [])
        live = [r for r in rows if r[3] != SegmentStatus.rejected]
        seg = None
        if rows:
            _, sid, text, st, conf, flagged, edited, created, kept, label = (live or rows)[0]
            if len(live) > 1:
                text = " ".join(r[2].strip() for r in live if r[2] and r[2].strip())
                conf = min((r[4] for r in live if r[4] is not None), default=None)
                label = None
            seg = {"id": sid, "text": text, "status": st, "confidence": conf, "flagged": flagged,
                   "reviewed": st == SegmentStatus.confirmed or edited is not None, "created": bool(created),
                   "spelling_kept": kept or [], "uploaded_differs": _uploaded_differs(text, label)}
        name = path.rsplit("/", 1)[-1]
        clips.append({"id": fid, "path": path, "stem": name.rsplit(".", 1)[0] if "." in name else name,
                      "duration": duration, "segments": len(live), "segment": seg})
    return {"clips": clips, "thresholds": _thresholds(session, ds.id)}


def _diariser(session: Session, segs: list[Segment]) -> str | None:
    """The segmentation model of the label run most of these segments came from (None without one)."""
    runs = Counter(s.source_run_id for s in segs if s.source_run_id is not None)
    if not runs:
        return None
    run = session.get(Run, runs.most_common(1)[0][0])
    model = session.get(Model, (run.params or {}).get("seg_model_id") or -1) if run else None
    return model.display_name if model else None


def file_view(session: Session, f: AudioFile) -> dict[str, Any]:
    segs = sorted(f.segments, key=lambda s: (s.start, s.end))
    ids = [i for s in segs for i in ((s.meta or {}).get("members") or {}).values()]
    outs = cache.outputs_for(session, ids)
    names = {m.catalog_id: m.display_name for m in session.scalars(select(Model))}
    rows = []
    for s in segs:
        d = segment_json(s)
        d["alternatives"] = [{"service": svc, "model": names.get(svc, svc), "text": outs[i].output.get("text", ""),
                              "build_key": outs[i].build_key, "source_app": outs[i].source_app}
                             for svc, i in ((s.meta or {}).get("members") or {}).items() if i in outs]
        rows.append(d)
    speakers = sorted({s.speaker for s in segs if s.speaker})
    siblings = [r[0] for r in session.execute(select(AudioFile.id).where(AudioFile.dataset_id == f.dataset_id)
                                              .order_by(AudioFile.rel_path))]
    k = siblings.index(f.id)
    return {"file": {"id": f.id, "path": f.rel_path, "stem": f.stem, "duration": f.duration, "status": f.status,
                     "dataset_id": f.dataset_id, "meta": f.meta},
            "dataset": {"id": f.dataset.id, "name": f.dataset.name, "language": f.dataset.language},
            "segments": rows, "speakers": speakers, "diariser": _diariser(session, segs), "keywords": [],
            "history": history.state(session, f.id),
            "thresholds": _thresholds(session, f.dataset_id),
            "prev_file_id": siblings[k - 1] if k > 0 else None,
            "next_file_id": siblings[k + 1] if k + 1 < len(siblings) else None,
            "position": {"index": k + 1, "of": len(siblings)}}
