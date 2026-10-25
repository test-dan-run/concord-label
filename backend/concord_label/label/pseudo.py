"""Ensemble pseudo-labelling as a `label` run (PLAN §6.2).

A label run is a seg -> ASR pipeline without a reference: the seg model's segments are merged and padded
(`cuts.prepare_cuts`, the same cuts Concord Bench's pipelines use), each cut is transcribed by every ASR
member, and the members are fused per cut.

It runs **stage by stage** (MODEL_CATALOG_REDESIGN.md "Job planning"), so each model loads once and only
one model is leased at a time:

1. the segmenter over every file, under one catalog lease on that model alone;
2. the cuts of every file (no model);
3. each ASR member over every cut, one lease per member, released before the next. The catalog's plan
   (`plan`) orders the members (running ones first, then by cold start);
4. fusion, triage and `write_segments` (no model).

Before a stage takes its lease, its outputs are looked up in batches (runs/cache.py): Label's own rows,
then the catalog's output index, where outputs Concord Bench already made are read from Bench and copied
here. A stage with nothing missing takes no lease ("Re-fuse 4 saved outputs"). Outputs are committed per
file, so a failed or interrupted run that is retried skips what it already did. A cancelled run stops
before its next stage or cut (`check_cancel`, runs/jobs.py), and fusion runs only when every stage finished.
How long a run has left is estimated stage by stage (label/eta.py) and reported through `on_eta`.

Writing segments follows one rule: reviewed work (confirmed, or touched by a person) is never replaced.
Earlier machine segments nobody touched are replaced; a new segment that mostly overlaps reviewed
work is dropped.

`run.params`: tasks (asr, diar), seg_model_id, asr_model_ids, strategy, single_model_id, high, low.
`run.outputs`: {"files": {file_id: {seg_cache_id, cuts: [{start, end, speaker, src, members}]}},
"summary": {...}}. `run.image_tags`: per model, the build it ran with ({build_key, image}).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

import httpx
from aicore.catalog import CatalogError
from aicore.catalog import span as index_span
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import adapters
from ..adapters.base import Adapter
from ..core import paths
from ..core.models import AudioFile, Dataset, Model, ModelOutput, Run, Segment, utcnow
from ..cuts import prepare_cuts, usable_files
from ..datasets.audio import cut, load_derived
from ..platform import catalog
from ..runs import cache
from ..runs.cache import Key
from . import fusion
from .eta import Eta, Stage, saved_rtf

Log = Callable[[str], None]
Progress = Callable[[float, str], None]
Check = Callable[[], None]  # raises when the run should stop

OVERLAP_DROP = 0.5  # a new segment overlapping reviewed work by more than this share of itself is dropped
MIN_ASR_SPAN = 0.05
SEG_SHARE = 0.3  # progress: stage 1 is the first 30 %, stage 2 runs to 90 %, fusion the rest


class LabelError(Exception):
    pass


class Leases:
    """How a run takes its models: one catalog lease per stage as `holder`. While the catalog queues the
    request, `ctx.waiting(lease)` marks the job waiting; `ctx.active()` when it is granted."""

    def __init__(self, holder: str, ctx: Any = None):
        self.holder = holder
        self.ctx = ctx

    @contextmanager
    def __call__(self, model_ids: list[str], log: Log) -> Iterator[Any]:
        with catalog.leased(self.holder, model_ids, log, on_wait=getattr(self.ctx, "waiting", None),
                            on_active=getattr(self.ctx, "active", None)) as lease:
            yield lease


def _models(session: Session, params: dict[str, Any]) -> tuple[Model, list[Model]]:
    def get(i: int | None) -> Model | None:
        try:
            return catalog.ensure_model(session, int(i)) if i is not None else None
        except (KeyError, ValueError):
            return None

    seg = get(params.get("seg_model_id"))
    if seg is None or not seg.is_seg:
        raise LabelError("pseudo-labelling needs a VAD / diarisation model for the segments")
    asr = [get(i) for i in params.get("asr_model_ids") or []]
    if any(m is None or not m.is_asr for m in asr):
        raise LabelError("every ensemble member must be a catalog ASR model")
    if "asr" in params.get("tasks", []) and not asr:
        raise LabelError("transcripts need at least one ASR model")
    if "diar" in params.get("tasks", []) and not seg.has_speakers:
        raise LabelError(f"{seg.display_name} returns no speakers; pick a diariser for the speakers task")
    return seg, asr  # type: ignore[return-value]


def asr_span(c_start: float, c_end: float, duration: float) -> tuple[float, float] | None:
    """The cache span of a cut (whole file -> (0, WHOLE)); None when too short."""
    if c_end - c_start < MIN_ASR_SPAN:
        return None
    if c_start <= 0.0 and c_end >= duration - 1e-3:
        return 0.0, cache.WHOLE
    return round(c_start, 3), round(c_end, 3)


def file_spans(seg_output: dict[str, Any], duration: float) -> list[tuple[Any, tuple[float, float] | None]]:
    """(cut, ASR span or None) for each cut of a file, from its segmenter output."""
    cuts = prepare_cuts(seg_output["segments"], seg_output.get("speakers"), duration)
    return [(c, asr_span(c.start, c.end, duration)) for c in cuts]


def _plan_keys(keys: list[Key], phash: str) -> list[dict[str, Any]]:
    out = []
    for sha, s, e in keys:
        a, b = index_span(s, e)
        out.append({"audio_sha256": sha, "span_start": a, "span_end": b, "params_hash": phash})
    return out


def plan(stages: list[dict[str, Any]], log: Log | None = None) -> dict[str, Any] | None:
    """The catalog's plan for these stages (order, loads, index hits); None when it does not answer."""
    try:
        out = catalog.get_catalog().plan(stages)
    except (CatalogError, httpx.HTTPError) as exc:
        if log:
            log(f"no plan from the catalog ({exc}); running in the chosen order")
        return None
    if log:
        steps = ", ".join(f"{s['model']}{' (skip)' if s.get('skip') else ''}" for s in out.get("steps") or [])
        log(f"plan: {steps}; {out.get('loads', 0)} load(s), about {out.get('est_load_s') or 0:.0f} s of cold starts")
    return out


def _brief(rec: dict[str, Any] | None) -> dict[str, Any]:
    rec = rec or {}
    return {"model_state": rec.get("state"), "cold_start_s": rec.get("cold_start_s"),
            "cold_start_measured": rec.get("cold_start_measured")}


def _record(m: Model) -> dict[str, Any] | None:
    try:
        return catalog.record(m.catalog_id)
    except (KeyError, catalog.CatalogUnavailable):
        return None


def _state(local: int, other: int, total: int, unknown: int = 0) -> str:
    """saved (all here) | bench (all here or in another app) | partial | new."""
    if unknown == 0 and local == total:
        return "saved"
    if unknown == 0 and local + other == total:
        return "bench"
    return "partial" if local + other else "new"


def preview(session: Session, ds: Dataset, seg_model: Model, asr_models: list[Model]) -> dict[str, Any]:
    """What a label run would cost: per model, how many outputs are saved here, how many Concord Bench holds
    (the catalog's output index: "saved in Bench"), and what is left to run. One index lookup per model.

    Cuts are known where the segmenter's output is saved here or in Bench; elsewhere every member needs
    inference."""
    files = usable_files(session, ds.id)
    seg = adapters.for_model(seg_model)
    sb = catalog.current_build(seg_model)
    seg_cache = cache.OutputCache(session, seg, sb["build_key"], sb["image"])
    seg_keys = {f.id: cache.key(f.sha256) for f in files}
    local = seg_cache.local(seg_keys.values())
    remote = seg_cache.peek([k for k in seg_keys.values() if k not in local])
    plans: list[tuple[AudioFile, list[tuple[float, float]] | None]] = []
    for f in files:
        k = seg_keys[f.id]
        out = local[k].output if k in local else (remote[k]["output"] if k in remote else None)
        if out is None:
            plans.append((f, None))
            continue
        plans.append((f, [sp for _, sp in file_spans(out, f.duration or 0.0) if sp]))
    members, stages_asr = [], {}
    for m in asr_models:
        a = adapters.for_model(m, ds.language)
        b = catalog.current_build(m)
        oc = cache.OutputCache(session, a, b["build_key"], b["image"])
        keys = [cache.key(f.sha256, s, e) for f, spans in plans if spans for s, e in spans]
        have = oc.local(keys)
        hits = oc.hits([k for k in keys if k not in have])
        unknown = sum(1 for _, spans in plans if spans is None)
        if unknown == 0:
            stages_asr[m.catalog_id] = _plan_keys(keys, a.params_hash)
        members.append({"model_id": m.id, "name": m.display_name, "service": m.catalog_id,
                        "timing_level": m.timing_level, "cached_cuts": len(have), "bench_cuts": len(hits),
                        "known_cuts": len(keys), "files_without_segments": unknown,
                        "state": _state(len(have), len(hits), len(keys), unknown), **_brief(_record(m))})
    seg_cached = len(local)
    seg_state = _state(seg_cached, len(remote), len(files))
    stages: list[dict[str, Any]] = [{"name": "seg", "models": [seg_model.catalog_id],
                                     "keys": {seg_model.catalog_id: _plan_keys(list(seg_keys.values()),
                                                                                 seg.params_hash)}}]
    if asr_models:
        stages.append({"name": "asr", "models": [m.catalog_id for m in asr_models], "after": "seg",
                       **({"keys": stages_asr} if len(stages_asr) == len(asr_models) else {})})
    pl = plan(stages)
    return {"files": len(files),
            "seg": {"model_id": seg_model.id, "name": seg_model.display_name, "service": seg_model.catalog_id,
                    "cached_files": seg_cached, "bench_files": len(remote), "state": seg_state,
                    **_brief(_record(seg_model))},
            "members": members,
            "saved_members": sum(1 for m in members if m["state"] in ("saved", "bench")),
            "bench_members": sum(1 for m in members if m["state"] == "bench"),
            "new_members": sum(1 for m in members if m["state"] not in ("saved", "bench")),
            "plan": None if pl is None else {"loads": pl.get("loads"), "est_load_s": pl.get("est_load_s"),
                                             "steps": pl.get("steps") or []}}


def required_models(pv: dict[str, Any]) -> list[str]:
    """Catalog models a label run has work for (the rest is saved here or in Bench): what it will lease."""
    names = [m["service"] for m in pv["members"] if m["state"] not in ("saved", "bench")]
    if pv["seg"]["state"] not in ("saved", "bench"):
        names.append(pv["seg"]["service"])
    return sorted(set(names))


# ---------------------------------------------------------------- execution

def run_stage(session: Session, model: Model, adapter: Adapter, work: list[tuple[AudioFile, list[Key]]],
              leases: Leases, log: Log, progress: Callable[[float, str | None], None],
              what: str, check_cancel: Check = lambda: None,
              eta: Eta | None = None) -> tuple[dict[Key, ModelOutput], int]:
    """One model over its work: (rows by key, outputs computed). Saved and copied outputs first, in one
    batch; the rest under one lease on this model alone, committed (and indexed) file by file."""
    b = catalog.current_build(model)
    oc = cache.OutputCache(session, adapter, b["build_key"], b["image"], log)
    keys = list(dict.fromkeys(k for _, ks in work for k in ks))
    found = oc.resolve(keys)
    session.commit()
    oc.record_index()
    if all(k in found for k in keys):
        log(f"{what}: all {len(keys)} outputs saved" + (f" ({oc.copied} copied from Concord Bench)" if oc.copied else "")
            + "; no inference")
        progress(1.0, None)
        if eta:
            eta.finish(model.catalog_id)
        return found, 0
    ran = 0
    check_cancel()
    with leases([model.catalog_id], log) as lease:
        fresh = catalog.current_build(model, force=True)
        if fresh["build_key"] != oc.build_key:  # e.g. the build was not known until the model started
            log(f"{what}: build {fresh['build_key']} (was {oc.build_key})")
            oc = cache.OutputCache(session, adapter, fresh["build_key"], fresh["image"], log)
            found = oc.resolve(keys)
            session.commit()
            oc.record_index()
        todo = [(f, [k for k in dict.fromkeys(ks) if k not in found]) for f, ks in work]
        todo = [(f, ks) for f, ks in todo if ks]
        total = sum(len(ks) for _, ks in todo)
        log(f"{what}: {len(keys) - total} of {len(keys)} outputs saved, {total} to run on {len(todo)} file(s)")
        if eta:
            eta.start(model.catalog_id, total, sum(key_audio_s(f, k) for f, ks in todo for k in ks))
        lost = False
        for f, ks in todo:
            if getattr(lease, "lost", None) and not lost:
                lost = True
                log(f"the lease on {model.catalog_id} was lost ({lease.lost}); calls still go through the gateway")
            audio = load_derived(paths.derived(f.sha256))
            for k in ks:
                check_cancel()
                samples = audio if k[2] == cache.WHOLE else cut(audio, k[1], k[2])
                found[k] = oc.run(samples, *k)
                ran += 1
                if eta:
                    eta.advance(model.catalog_id, key_audio_s(f, k))
            session.commit()  # keep outputs (a retry skips them), release the write lock
            oc.record_index()
            progress(ran / total, f"{what}: {f.rel_path}")
    if eta:
        eta.finish(model.catalog_id)
    return found, ran


def key_audio_s(f: AudioFile, k: Key) -> float:
    """Seconds of audio in a call: the whole file, or the cut."""
    return (f.duration or 0.0) if k[2] == cache.WHOLE else k[2] - k[1]


def eta_stages(session: Session, run: Run, seg_model: Model, asr_models: list[Model],
               files: list[AudioFile]) -> list[Stage]:
    """The run's stages for its estimate. A stage the preview found saved has no work; until the cuts are
    known, an ASR stage counts all the audio."""
    states = (run.params.get("preview") or {}).get("states") or {}
    audio = sum(f.duration or 0.0 for f in files)
    out = []
    for m, kind in [(seg_model, "seg"), *((a, "asr") for a in asr_models)]:
        rec = _record(m) or {}
        cold = 0.0 if rec.get("state") == "running" else float(rec.get("cold_start_s") or 0.0)
        work = 0.0 if states.get(m.catalog_id) in ("saved", "bench") else audio
        out.append(Stage(m.catalog_id, kind, work, rtf=saved_rtf(session, m.catalog_id), cold_s=cold))
    return out


def execute(session: Session, run: Run, log: Log, progress: Progress, leases: Leases | None = None,
            check_cancel: Check = lambda: None, on_eta: Callable[[dict[str, Any]], None] | None = None) -> None:
    leases = leases or Leases(f"{catalog.APP}:run-{run.id}")
    ds: Dataset = run.dataset
    p = run.params
    seg_model, asr_models = _models(session, p)
    tasks = set(p.get("tasks") or ["asr", "diar"])
    files = usable_files(session, ds.id)
    if not files:
        raise LabelError("the dataset has no usable audio files")
    if "asr" not in tasks:
        asr_models = []
    seg = adapters.for_model(seg_model)
    members = {m.catalog_id: adapters.for_model(m, ds.language) for m in asr_models}
    run.status, run.started_at = "running", utcnow()
    run.image_tags = {m.catalog_id: catalog.current_build(m) for m in [seg_model, *asr_models]}
    run.params = {**p, "seg_adapter": seg.params, "asr_adapters": {n: a.params for n, a in members.items()}}
    session.commit()
    eta = Eta(eta_stages(session, run, seg_model, asr_models, files), on_eta)
    eta.report(force=True)

    # stage 1: the segmenter over every file
    def seg_progress(x: float, msg: str | None) -> None:
        progress(SEG_SHARE * x, msg or f"segments from {seg_model.display_name}")

    seg_progress(0.0, f"stage 1: {seg_model.display_name} on {len(files)} files")
    seg_rows, seg_ran = run_stage(session, seg_model, seg, [(f, [cache.key(f.sha256)]) for f in files], leases,
                                  log, seg_progress, f"stage 1, {seg_model.catalog_id}", check_cancel, eta)

    # the cuts of every file
    cuts: dict[int, list[tuple[Any, tuple[float, float] | None]]] = {}
    for f in files:
        cuts[f.id] = file_spans(seg_rows[cache.key(f.sha256)].output, f.duration or 0.0)
    work = [(f, [cache.key(f.sha256, *sp) for _, sp in cuts[f.id] if sp]) for f in files]
    cut_audio = {k: key_audio_s(f, k) for f, ks in work for k in ks}
    n_keys = len(cut_audio)
    eta.asr_audio(sum(cut_audio.values()))

    # stage 2: each ASR member over every cut, one at a time, in the catalog's order
    order = list(members)
    if len(order) > 1:
        pl = plan([{"name": "asr", "models": order, "keys": {n: _plan_keys(list({k for _, ks in work for k in ks}),
                                                                           members[n].params_hash) for n in order}}],
                  log)
        planned = [s["model"] for s in (pl or {}).get("steps") or [] if s.get("model") in members]
        order = list(dict.fromkeys(planned + order))
        eta.order(order)
    by_id = {m.catalog_id: m for m in asr_models}
    member_rows: dict[str, dict[Key, ModelOutput]] = {}
    calls = ran_asr = 0
    for i, name in enumerate(order):
        lo = SEG_SHARE + (0.9 - SEG_SHARE) * i / len(order)
        hi = SEG_SHARE + (0.9 - SEG_SHARE) * (i + 1) / len(order)

        def asr_progress(x: float, msg: str | None, lo: float = lo, hi: float = hi, name: str = name) -> None:
            progress(lo + (hi - lo) * x, msg or name)

        asr_progress(0.0, f"stage 2: {by_id[name].display_name} on {n_keys} cuts")
        member_rows[name], ran = run_stage(session, by_id[name], members[name], work, leases, log, asr_progress,
                                           f"stage 2, {name}", check_cancel, eta)
        calls += n_keys
        ran_asr += ran

    # fusion: no model
    check_cancel()
    out_files: dict[str, Any] = {}
    seg_s = asr_s = audio_s = 0.0
    for f in files:
        row = seg_rows[cache.key(f.sha256)]
        seg_s += row.latency_s
        entry: dict[str, Any] = {"seg_cache_id": row.id, "cuts": []}
        for c, sp in cuts[f.id]:
            cd: dict[str, Any] = {"start": c.start, "end": c.end, "speaker": c.speaker, "src": c.src, "members": {}}
            if sp is not None:
                k = cache.key(f.sha256, *sp)
                for name in members:
                    mo = member_rows[name][k]
                    cd["members"][name] = mo.id
                    asr_s += mo.latency_s
            entry["cuts"].append(cd)
        out_files[str(f.id)] = entry
        audio_s += f.duration or 0.0
    run.outputs = {"files": out_files}
    run.rtf_by_stage = {"seg": seg_s / audio_s if audio_s else None, "asr": asr_s / audio_s if audio_s else None,
                        "total": (seg_s + asr_s) / audio_s if audio_s else None, "audio_s": audio_s,
                        "processing_s": seg_s + asr_s}
    log(f"label: {len(files)} files, segmenter ran on {seg_ran}; {calls} ASR cuts, {ran_asr} run "
        f"({calls - ran_asr} saved)")
    progress(0.9, "fusing")
    summary = write_segments(session, run, files)
    run.outputs = {"files": out_files, "summary": summary}
    run.finished_at = utcnow()
    session.flush()
    t = summary["triage"]
    log(f"fused {summary['segments']} segments: {t.get('auto', 0)} auto-accepted, {t.get('pseudo', 0)} to review, "
        f"{t.get('rejected', 0)} rejected, {summary['kept_reviewed']} left to reviewed work")


def is_machine(s: Segment) -> bool:
    """Written by pseudo-labelling, here or in another install (imported packages drop the run)."""
    return s.source_run_id is not None or (s.meta or {}).get("source") == "pseudo-label"


def _overlap(a0: float, a1: float, b0: float, b1: float) -> float:
    return max(0.0, min(a1, b1) - max(a0, b0))


def write_segments(session: Session, run: Run, files: list[AudioFile]) -> dict[str, Any]:
    """Fuse the run's member outputs per cut and write the segments (see the module docstring)."""
    p = run.params
    tasks = set(p.get("tasks") or ["asr", "diar"])
    strategy = p.get("strategy") or "best_aligned"
    single = None
    if strategy == "single":
        m = session.get(Model, p.get("single_model_id") or -1)
        single = m.catalog_id if m else None
    high, low = float(p.get("high", fusion.DEFAULT_HIGH)), float(p.get("low", fusion.DEFAULT_LOW))
    services = list((p.get("asr_adapters") or {}).keys())
    ids = [i for e in run.outputs["files"].values() for c in e["cuts"] for i in c["members"].values()]
    outs = cache.outputs_for(session, ids)
    matrix = fusion.AgreementMatrix(services)
    chosen: Counter[str] = Counter()
    applied: Counter[str] = Counter()
    triage: Counter[str] = Counter()
    kept_reviewed = replaced = written = 0
    by_id = {f.id: f for f in files}
    for fid, entry in run.outputs["files"].items():
        f = by_id.get(int(fid))
        if f is None:
            continue
        existing = list(session.scalars(select(Segment).where(Segment.audio_file_id == f.id)))
        reviewed = [s for s in existing if s.reviewed]
        for s in existing:
            if not s.reviewed and is_machine(s):
                session.delete(s)
                replaced += 1
        for c in entry["cuts"]:
            s0, s1 = c["src"]
            if s1 - s0 <= 0:
                continue
            if any(_overlap(s0, s1, r.start, r.end) > OVERLAP_DROP * (s1 - s0) for r in reviewed):
                kept_reviewed += 1
                continue
            meta: dict[str, Any] = {"span": [c["start"], c["end"]], "members": c["members"]}
            text = conf = None
            if "asr" in tasks:
                hyps = []
                for svc in services:
                    row = outs.get(c["members"].get(svc))
                    if row is None:
                        continue
                    o = row.output
                    off = 0.0 if row.span_end < 0 else row.span_start
                    words = [{"w": w["w"], "start": round(w["start"] + off, 3), "end": round(w["end"] + off, 3)}
                             for w in o.get("words") or []] or None
                    hyps.append(fusion.Hyp(svc, o.get("text") or "", words))
                if not hyps:
                    continue
                fz = fusion.fuse(hyps, strategy, single)
                matrix.add(hyps)
                chosen[fz.chosen] += 1
                applied[fz.strategy] += 1
                text, conf = fz.text, fz.confidence
                meta.update({"chosen": fz.chosen, "strategy": fz.strategy, "agreement": fz.agreement,
                             "words": fz.words})
            status = fusion.triage(conf, high, low)
            triage[status] += 1
            session.add(Segment(audio_file_id=f.id, start=round(s0, 3), end=round(s1, 3),
                                speaker=c["speaker"] if "diar" in tasks else None, text=text, status=status,
                                confidence=None if conf is None else round(conf, 4), source_run_id=run.id,
                                meta=meta))
            written += 1
        session.flush()
    total = sum(chosen.values())
    return {"segments": written, "files": len(run.outputs["files"]), "triage": dict(triage),
            "kept_reviewed": kept_reviewed, "replaced": replaced, "agreement": matrix.to_json(),
            "chosen": [{"member": k, "n": v, "share": v / total if total else None} for k, v in chosen.most_common()],
            "strategy_applied": dict(applied), "thresholds": {"high": high, "low": low}}
