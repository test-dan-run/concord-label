"""Label (Phase 2): review console, ensemble pseudo-labelling, annotation, live evaluation."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.db import get_session
from ..core.models import AudioFile, Job, Model, Run, RunTask, Segment, SegmentStatus
from ..label import console, edit, fusion, live, pseudo
from ..platform import catalog
from ..runs import jobs
from .datasets import dataset_json, get_dataset, job_brief
from .jobs import ACTIVE, run_json

router = APIRouter(prefix="/label", tags=["label"])


@router.get("/console")
def review_console(session: Session = Depends(get_session)) -> list[dict[str, Any]]:
    return console.console(session)


# ---------------------------------------------------------------- pseudo-labelling

ModelRef = int | str  # Label's model id, or a catalog model id (a row is made on first use)


def _model(session: Session, ref: ModelRef) -> Model:
    if isinstance(ref, str) and ref.isdigit():
        ref = int(ref)
    try:
        return catalog.ensure_model(session, ref)
    except KeyError as exc:
        raise HTTPException(422, str(exc.args[0])) from None
    except catalog.CatalogUnavailable as exc:
        raise HTTPException(503, str(exc)) from None


def _models(session: Session, seg_ref: ModelRef | None, asr_refs: list[ModelRef]) -> tuple[Model, list[Model]]:
    if seg_ref is None or seg_ref == "":
        raise HTTPException(422, "pick a VAD / diarisation model for the segments")
    seg = _model(session, seg_ref)
    if not seg.is_seg:
        raise HTTPException(422, f"{seg.display_name} is not a VAD / diarisation model in the catalog")
    asr = []
    for ref in asr_refs:
        m = _model(session, ref)
        if not m.is_asr:
            raise HTTPException(422, f"{m.display_name} is not an ASR model in the catalog")
        asr.append(m)
    return seg, asr


def _refs(csv: str) -> list[ModelRef]:
    """`3,whisper-large-v3` -> [3, "whisper-large-v3"]: numbers are Label's ids, the rest catalog ids."""
    return [int(x) if x.strip().isdigit() else x.strip() for x in csv.split(",") if x.strip()]


@router.get("/datasets/{dataset_id}/preview")
def label_preview(dataset_id: int, seg_model_id: str, asr_model_ids: str = "",
                  session: Session = Depends(get_session)) -> dict[str, Any]:
    """Cost of a label run: which outputs are saved here or in Concord Bench, and the catalog's plan.
    Model ids are Label's ids or catalog ids."""
    ds = get_dataset(session, dataset_id)
    seg, asr = _models(session, (_refs(seg_model_id) or [None])[0], _refs(asr_model_ids))
    pv = pseudo.preview(session, ds, seg, asr)
    session.commit()  # model rows made on first use
    return pv


@router.get("/datasets/{dataset_id}/runs")
def list_label_runs(dataset_id: int, session: Session = Depends(get_session)) -> list[dict[str, Any]]:
    """The dataset's label runs, newest first."""
    get_dataset(session, dataset_id)
    runs = session.scalars(select(Run).where(Run.task == RunTask.label, Run.dataset_id == dataset_id)
                           .order_by(Run.id.desc()))
    return [run_json(r) for r in runs]


class LabelRunIn(BaseModel):
    """Models by Label id or catalog id (pickers send catalog ids)."""

    tasks: list[str] = Field(default_factory=lambda: ["asr", "diar"])
    seg_model_id: int | str
    asr_model_ids: list[int | str] = Field(default_factory=list)
    strategy: str = "best_aligned"
    single_model_id: int | str | None = None
    high: float = Field(default=fusion.DEFAULT_HIGH, ge=0, le=1)
    low: float = Field(default=fusion.DEFAULT_LOW, ge=0, le=1)


@router.post("/datasets/{dataset_id}/runs", status_code=202)
def start_label_run(dataset_id: int, body: LabelRunIn, session: Session = Depends(get_session)) -> dict[str, Any]:
    ds = get_dataset(session, dataset_id)
    if ds.status != "ready":
        raise HTTPException(409, f"dataset {ds.name!r} is {ds.status}")
    tasks = [t for t in body.tasks if t in ("asr", "diar")]
    if not tasks:
        raise HTTPException(422, "choose at least one task: asr (transcripts) or diar (speakers)")
    if body.strategy not in fusion.STRATEGIES:
        raise HTTPException(422, f"strategy must be one of {', '.join(fusion.STRATEGIES)}")
    if body.low > body.high:
        raise HTTPException(422, "the review threshold must not be above the auto-accept threshold")
    seg, asr = _models(session, body.seg_model_id, body.asr_model_ids if "asr" in tasks else [])
    if "asr" in tasks and not asr:
        raise HTTPException(422, "transcripts need at least one ASR model")
    if "diar" in tasks and not seg.has_speakers:
        raise HTTPException(422, f"{seg.display_name} returns no speakers; pick a diariser for the speakers task")
    single = next((m for m in asr if body.single_model_id in (m.id, m.catalog_id)), None)
    if body.strategy == "single" and single is None:
        raise HTTPException(422, "the single-model strategy needs one of the chosen ASR models")
    active = session.scalar(select(Run).where(Run.task == RunTask.label, Run.dataset_id == ds.id,
                                              Run.status.in_(ACTIVE)))
    if active is not None:
        raise HTTPException(409, f"a pseudo-label run is already {active.status} on this dataset (run {active.id})")
    pv = pseudo.preview(session, ds, seg, asr)
    run = Run(task=RunTask.label, dataset_id=ds.id, status="queued",
              params={"tasks": tasks, "seg_model_id": seg.id, "asr_model_ids": [m.id for m in asr],
                      "models": {str(m.id): m.catalog_id for m in [seg, *asr]},
                      "strategy": body.strategy, "single_model_id": single.id if single else None,
                      "high": body.high, "low": body.low,
                      "preview": {"saved_members": pv["saved_members"], "bench_members": pv["bench_members"],
                                  "new_members": pv["new_members"], "plan": pv["plan"]}})
    session.add(run)
    session.flush()
    session.refresh(run)
    job = jobs.enqueue(session, "label", {"run_id": run.id}, pseudo.required_models(pv))
    session.commit()
    return {"run": run_json(run), "job": job_brief(job)}


def _queue(session: Session, dataset_id: int, limit: int = 50) -> list[dict[str, Any]]:
    rows = session.execute(
        select(Segment, AudioFile.rel_path).join(AudioFile).where(
            AudioFile.dataset_id == dataset_id, Segment.edited_at.is_(None),
            Segment.status.in_((SegmentStatus.pseudo, SegmentStatus.rejected)))
        .order_by(Segment.confidence.asc().nulls_first(), Segment.id).limit(limit)).all()
    return [{**edit.segment_json(s), "file": p} for s, p in rows]


@router.get("/datasets/{dataset_id}/results")
def label_results(dataset_id: int, session: Session = Depends(get_session)) -> dict[str, Any]:
    """The latest label run (its triage, agreement matrix, chosen shares), today's progress, and the review
    queue (unreviewed segments, lowest confidence first)."""
    ds = get_dataset(session, dataset_id)
    run = console.latest_label_run(session, ds.id)
    job = None
    if run is not None:
        job = session.scalar(select(Job).where(Job.type == "label").where(Job.payload["run_id"].as_integer() == run.id)
                             .order_by(Job.id.desc()))
    names = {m.catalog_id: m.display_name for m in session.scalars(select(Model))}
    seg = session.get(Model, run.params.get("seg_model_id") or -1) if run else None
    prog = console.progress(session, [ds])[ds.id]
    return {"dataset": dataset_json(session, ds), "run": run_json(run) if run else None,
            "job": job_brief(job) if job else None,
            "summary": (run.outputs or {}).get("summary") if run else None, "model_names": names,
            "segmenter": seg.display_name if seg else None,
            "progress": prog, "next": console.next_action(ds, prog), "queue": _queue(session, ds.id)}


# ---------------------------------------------------------------- annotate

def _file(session: Session, file_id: int) -> AudioFile:
    f = session.get(AudioFile, file_id)
    if f is None:
        raise HTTPException(404, "audio file not found")
    return f


def _segment(session: Session, segment_id: int) -> Segment:
    s = session.get(Segment, segment_id)
    if s is None:
        raise HTTPException(404, "segment not found")
    return s


def _segments(session: Session, ids: list[int]) -> list[Segment]:
    segs = list(session.scalars(select(Segment).where(Segment.id.in_(ids))))
    if len(segs) != len(set(ids)):
        raise HTTPException(404, "some segments were not found")
    return segs


@router.get("/datasets/{dataset_id}/files")
def label_files(dataset_id: int, session: Session = Depends(get_session)) -> list[dict[str, Any]]:
    get_dataset(session, dataset_id)
    return edit.file_counts(session, dataset_id)


@router.get("/files/{file_id}")
def annotate_file(file_id: int, session: Session = Depends(get_session)) -> dict[str, Any]:
    return edit.file_view(session, _file(session, file_id))


class SegmentPatch(BaseModel):
    text: str | None = None
    speaker: str | None = None
    start: float | None = None
    end: float | None = None
    status: str | None = None
    flagged: bool | None = None
    flag_note: str | None = None


def _commit_or_400(session: Session, fn):
    try:
        out = fn()
    except edit.EditError as exc:
        session.rollback()
        raise HTTPException(422, str(exc)) from None
    session.commit()
    return out


@router.patch("/segments/{segment_id}")
def patch_segment(segment_id: int, body: SegmentPatch, session: Session = Depends(get_session)) -> dict[str, Any]:
    s = _segment(session, segment_id)
    fields = body.model_dump(exclude_unset=True)
    _commit_or_400(session, lambda: edit.update(session, s, fields))
    return edit.segment_json(s)


class BulkIn(BaseModel):
    ids: list[int] = Field(min_length=1)
    action: str  # confirm | reject | review | assign_speaker | flag | unflag
    speaker: str | None = None


@router.post("/segments/bulk")
def bulk_segments(body: BulkIn, session: Session = Depends(get_session)) -> dict[str, Any]:
    segs = _segments(session, body.ids)
    n = _commit_or_400(session, lambda: edit.bulk(session, segs, body.action, body.speaker))
    return {"updated": n, "segments": [edit.segment_json(s) for s in segs]}


class MergeIn(BaseModel):
    ids: list[int] = Field(min_length=2)


@router.post("/segments/merge")
def merge_segments(body: MergeIn, session: Session = Depends(get_session)) -> dict[str, Any]:
    segs = _segments(session, body.ids)
    s = _commit_or_400(session, lambda: edit.merge(session, segs))
    return edit.segment_json(s)


class SplitIn(BaseModel):
    t: float


@router.post("/segments/{segment_id}/split")
def split_segment(segment_id: int, body: SplitIn, session: Session = Depends(get_session)) -> list[dict[str, Any]]:
    s = _segment(session, segment_id)
    a, b = _commit_or_400(session, lambda: edit.split(session, s, body.t))
    return [edit.segment_json(a), edit.segment_json(b)]


@router.delete("/segments/{segment_id}", status_code=204)
def delete_segment(segment_id: int, session: Session = Depends(get_session)) -> None:
    session.delete(_segment(session, segment_id))
    session.commit()


class SegmentIn(BaseModel):
    start: float
    end: float
    speaker: str | None = None
    text: str | None = None


@router.post("/files/{file_id}/segments", status_code=201)
def create_segment(file_id: int, body: SegmentIn, session: Session = Depends(get_session)) -> dict[str, Any]:
    f = _file(session, file_id)
    s = _commit_or_400(session, lambda: edit.create(session, f, body.start, body.end, body.speaker, body.text))
    return edit.segment_json(s)


@router.post("/files/{file_id}/confirm-auto")
def confirm_auto(file_id: int, session: Session = Depends(get_session)) -> dict[str, Any]:
    f = _file(session, file_id)
    return {"confirmed": _commit_or_400(session, lambda: edit.confirm_auto(session, f))}


# ---------------------------------------------------------------- live evaluation

@router.get("/datasets/{dataset_id}/live")
def live_eval(dataset_id: int, normaliser: str | None = None, collar: float | None = None,
              session: Session = Depends(get_session)) -> dict[str, Any]:
    ds = get_dataset(session, dataset_id)
    try:
        return live.live(session, ds, normaliser, collar)
    except KeyError as exc:
        raise HTTPException(400, str(exc)) from None
