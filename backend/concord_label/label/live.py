"""Live evaluation while labelling (PLAN §6.4): model scores against the segments confirmed so far.

- References are confirmed segments with text, in the order they were confirmed.
- A model's hypothesis for a segment is its cached output on exactly that span from the model's current
  build (catalog build key; a new build simply has no outputs yet), else the member output pseudo-labelling
  stored for it when it is from that build too. Nothing is inferred here, so the page is cheap to refresh.
- Below MIN_RANK confirmed segments the standings say "too few to rank". 95% intervals are a
  percentile bootstrap over segments (corpus WER of each resample).
- DER uses only fully reviewed files (every segment reviewed), so missing labels never count as misses.
- cpWER (concatenated minimum-permutation WER, metrics/cpwer.py) scores each file up to where its review has got
  (`reviewed_until`: the start of the first segment nobody has reviewed, or the whole file), so a file counts
  before it is finished. The reference is the confirmed segments before that point, and the hypothesis of an ASR
  model is the latest done label run's cuts before it, with the diariser's speakers and the model's text on each
  cut; a segment or a cut is before the point when its middle is. So cpWER scores the diariser and the ASR model
  together. A model counts on a file only when it has an output from its current build for every cut scored
  there. A word given to the wrong speaker counts twice.
- The locked benchmark score comes from Concord Bench (SPLIT_PLAN.md §5.3, CONTRACTS.md §4): one request
  per page load, for the newest version Bench holds of this dataset's Dataset Catalog dataset, matched to models
  by catalog model id (the platform service name, the same in both apps).
  Without a reachable Bench, or for a dataset that has no catalog dataset or no version in Bench, it is None
  and the page hides it.
  A model with a locked score but no outputs here for the confirmed segments (a dataset uploaded with its
  references) is listed with the locked score alone.
"""

from __future__ import annotations

from typing import Any

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import adapters, peer
from ..core.models import AudioFile, Dataset, Model, Segment, SegmentStatus
from ..cuts import usable_files
from ..metrics import cpwer, der, normalisers
from ..metrics.der import Turn
from ..platform import catalog
from ..runs import cache
from . import console, fusion

MIN_RANK = 30
BOOTSTRAP = 1000
CURVE_POINTS = 60


def bootstrap_ci(errors: np.ndarray, words: np.ndarray, n: int = BOOTSTRAP, seed: int = 0) -> tuple[float, float] | None:
    if len(errors) < 2 or words.sum() == 0:
        return None
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(errors), size=(n, len(errors)))
    w = words[idx].sum(axis=1)
    keep = w > 0
    vals = errors[idx].sum(axis=1)[keep] / w[keep]
    lo, hi = np.percentile(vals, [2.5, 97.5])
    return float(lo), float(hi)


def _confirmed(session: Session, files: list[AudioFile]) -> list[Segment]:
    segs = [s for f in files for s in f.segments if s.status == SegmentStatus.confirmed and s.text is not None]
    return sorted(segs, key=lambda s: (s.edited_at or s.updated_at, s.id))


def _locked(ds: Dataset, nkey: str) -> dict[str, dict[str, Any]]:
    """Each model's locked benchmark score in Concord Bench, by catalog model id: Bench finds the dataset by its
    Dataset Catalog id (DATASET_CATALOG_PLAN.md §8.4). {} without Bench, or for a dataset never published to
    the catalog or made from it."""
    if not ds.catalog_dataset_id:
        return {}
    return peer.locked_scores(ds.catalog_dataset_id, nkey) or {}


def asr_standings(session: Session, ds: Dataset, files: list[AudioFile], normaliser: str | None) -> dict[str, Any]:
    norm = normalisers.get(normaliser or normalisers.DEFAULT)
    segs = _confirmed(session, files)
    sha = {f.id: f.sha256 for f in files}
    dur = {f.id: f.duration or 0.0 for f in files}
    refs = norm.many([s.text or "" for s in segs])
    models = []
    member_ids = [i for s in segs for i in ((s.meta or {}).get("members") or {}).values()]
    member_rows = cache.outputs_for(session, member_ids)
    locked = _locked(ds, norm.key)
    for m in session.scalars(select(Model).order_by(Model.display_name)):
        if not m.is_asr:
            continue
        try:
            a = adapters.for_model(m, ds.language)
        except adapters.AdapterError:
            continue
        build = catalog.current_build(m)["build_key"]
        have = cache.local_rows(session, a.service, build, a.params_hash, list(set(sha.values())))
        hyps: list[str | None] = []
        for s in segs:
            whole = s.start <= 0.0 and s.end >= dur[s.audio_file_id] - 1e-3
            row = have.get((sha[s.audio_file_id], 0.0, cache.WHOLE) if whole else
                           (sha[s.audio_file_id], round(s.start, 3), round(s.end, 3)))
            if row is None:
                mid = ((s.meta or {}).get("members") or {}).get(a.service)
                row = member_rows.get(mid) if mid else None
                if row is not None and (not build or row.build_key != build):
                    row = None  # another build's output: a new build is scored on its own outputs only
            hyps.append(row.output.get("text", "") if row else None)
        covered = [k for k, h in enumerate(hyps) if h is not None]
        if not covered:
            if locked.get(m.catalog_id):
                # No outputs here for the confirmed segments (references uploaded with the dataset, say), but
                # Bench has benchmarked the model on a package of this dataset: list it with that score alone.
                models.append({"model_id": m.id, "name": m.display_name, "service": m.catalog_id, "coverage": 0,
                               "words": 0, "wer": None, "ci": None, "curve": [], "locked": locked[m.catalog_id]})
            continue
        hn = norm.many([hyps[k] or "" for k in covered])
        err = np.zeros(len(segs), dtype=float)
        words = np.zeros(len(segs), dtype=float)
        for k, h in zip(covered, hn, strict=True):
            err[k], words[k] = fusion.errors(refs[k], h)
        has = np.zeros(len(segs), dtype=bool)
        has[covered] = True
        cum_e, cum_n = np.cumsum(err), np.cumsum(words)
        step = max(1, len(segs) // CURVE_POINTS)
        curve = [{"confirmed": k + 1, "wer": float(cum_e[k] / cum_n[k]) if cum_n[k] else None}
                 for k in range(len(segs)) if (k + 1) % step == 0 or k == len(segs) - 1]
        tot_n = words[has].sum()
        models.append({"model_id": m.id, "name": m.display_name, "service": m.catalog_id,
                       "coverage": len(covered), "words": int(tot_n),
                       "wer": float(err[has].sum() / tot_n) if tot_n else None,
                       "ci": bootstrap_ci(err[has], words[has]) if len(segs) >= MIN_RANK else None,
                       "curve": curve, "locked": locked.get(m.catalog_id)})
    # Catalog ASR models Label has never used have no outputs here; Bench's locked score still lists them.
    seen = {m["service"] for m in models} | set(session.scalars(select(Model.catalog_id)))
    try:
        records = catalog.models() if locked else []
    except catalog.CatalogUnavailable:
        records = []
    for rec in records:
        if rec["id"] not in seen and locked.get(rec["id"]) and "asr" in catalog.capabilities(rec)[0]:
            models.append({"model_id": None, "name": rec.get("display_name") or rec["id"], "service": rec["id"],
                           "coverage": 0, "words": 0, "wer": None, "ci": None, "curve": [], "locked": locked[rec["id"]]})
    ranked = len(segs) >= MIN_RANK
    models.sort(key=lambda r: (r["wer"] is None, r["wer"] if r["wer"] is not None else 0))
    for k, r in enumerate(models):
        r["rank"] = k + 1 if ranked and r["wer"] is not None else None
    return {"normaliser": norm.key, "confirmed": len(segs), "min_rank": MIN_RANK, "ranked": ranked, "models": models}


def _complete(files: list[AudioFile]) -> list[AudioFile]:
    """Fully reviewed files with speakers: every segment reviewed, and a confirmed segment with a speaker."""
    return [f for f in files if f.segments and all(s.reviewed for s in f.segments)
            and any(s.speaker for s in f.segments if s.status == SegmentStatus.confirmed)]


def reviewed_until(f: AudioFile) -> float:
    """How far into the file the review has got: the start of the first segment nobody has reviewed, or the end of
    the file when every segment is reviewed."""
    first = min((s.start for s in f.segments if not s.reviewed), default=None)
    return (f.duration or 0.0) if first is None else first


def _before(start: float, end: float, t: float) -> bool:
    return (start + end) / 2 < t


def cp_card(session: Session, ds: Dataset, files: list[AudioFile], normaliser: str | None) -> dict[str, Any]:
    """cpWER of each ASR model with the speakers of the latest label run's diariser, on each file up to where its
    review has got."""
    norm = normalisers.get(normaliser or normalisers.DEFAULT)
    until = {f.id: reviewed_until(f) for f in files}
    scored = [f for f in files if any(s.speaker and _before(s.start, s.end, until[f.id]) for s in f.segments
                                      if s.status == SegmentStatus.confirmed)]
    out: dict[str, Any] = {"files": len(scored), "of": len(files), "complete": len(_complete(files)),
                           "seconds": sum(min(until[f.id], f.duration or 0.0) for f in scored),
                           "diariser": None, "reason": None, "models": []}
    run = console.latest_label_run(session, ds.id, done=True)
    if run is None:
        out["reason"] = "Pseudo-label the dataset first: cpWER uses the speakers its diariser found."
        return out
    seg = session.get(Model, (run.params or {}).get("seg_model_id") or -1)
    out["diariser"] = seg.display_name if seg else None
    if "diar" not in ((run.params or {}).get("tasks") or ["asr", "diar"]) or (seg is not None and not seg.has_speakers):
        out["reason"] = "The last pseudo-label run found no speakers, so there is nothing to score cpWER on."
        return out
    if not scored:
        out["reason"] = ("cpWER needs confirmed segments with speakers at the start of a file. It scores each file up to "
                         "the first segment nobody has reviewed, so unlabelled speech never counts as errors.")
        return out
    cuts = {f.id: [c for c in (run.outputs or {}).get("files", {}).get(str(f.id), {}).get("cuts") or []
                   if _before(c["src"][0], c["src"][1], until[f.id])] for f in scored}
    rows_by_id = cache.outputs_for(session, [i for cs in cuts.values() for c in cs for i in c["members"].values()])
    refs: dict[int, list[cpwer.SpkSegment]] = {}
    for f in scored:
        segs = [s for s in f.segments if s.status == SegmentStatus.confirmed and s.text is not None
                and _before(s.start, s.end, until[f.id])]
        refs[f.id] = [cpwer.SpkSegment(s.start, s.end, s.speaker or "(no speaker)", t)
                      for s, t in zip(segs, norm.many([s.text or "" for s in segs]), strict=True)]
    every = (run.outputs or {}).get("files", {}).values()
    services = sorted({svc for e in every for c in e.get("cuts") or [] for svc in c["members"]})
    models = {m.catalog_id: m for m in session.scalars(select(Model).where(Model.catalog_id.in_(services)))}
    for svc in services:
        m = models.get(svc)
        if m is None or not m.is_asr:
            continue
        build = catalog.current_build(m)["build_key"]
        rows = []
        for f in scored:
            outs = [rows_by_id.get(c["members"].get(svc)) for c in cuts[f.id]]
            if any(o is None or not build or o.build_key != build for o in outs):
                continue  # no output from the model's current build for some cut scored in this file
            texts = norm.many([o.output.get("text", "") for o in outs])
            hyp = [cpwer.SpkSegment(c["src"][0], c["src"][1], c["speaker"] or "(no speaker)", t)
                   for c, t in zip(cuts[f.id], texts, strict=True)]
            rows.append(cpwer.public(cpwer.cpwer(refs[f.id], hyp)))
        if rows:
            agg = cpwer.aggregate(rows)
            out["models"].append({"model_id": m.id, "name": m.display_name, "service": svc, "cpwer": agg["cpwer"],
                                  "errors": agg["errors"], "words": agg["n"], "files": agg["files"]})
    out["models"].sort(key=lambda r: (r["cpwer"] is None, r["cpwer"] or 0))
    return out


def der_card(session: Session, files: list[AudioFile], collar: float) -> dict[str, Any]:
    complete = _complete(files)
    out: dict[str, Any] = {"files": len(complete), "of": len(files), "collar": collar, "models": []}
    if not complete:
        return out
    shas = [f.sha256 for f in complete]
    for m in session.scalars(select(Model).order_by(Model.display_name)):
        if not m.has_speakers:
            continue
        try:
            a = adapters.for_model(m)
        except adapters.AdapterError:
            continue
        have = cache.local_rows(session, a.service, catalog.current_build(m)["build_key"], a.params_hash, shas)
        rows = []
        for f in complete:
            row = have.get((f.sha256, 0.0, cache.WHOLE))
            if row is None:
                continue
            ref = [Turn(s.start, s.end, s.speaker) for s in f.segments if s.status == SegmentStatus.confirmed and s.speaker]
            hyp = [Turn(s, e, k) for (s, e), k in zip(row.output["segments"], row.output.get("speakers") or [],
                                                      strict=False)]
            d = der.der(ref, hyp, f.duration or 0.0, collar)
            rows.append({k: v for k, v in d.items() if k != "mapping"})
        if rows:
            agg = der.aggregate(rows)
            out["models"].append({"model_id": m.id, "name": m.display_name, "der": agg["der"], "jer": agg["jer"],
                                  "files": agg["files"]})
    out["models"].sort(key=lambda r: (r["der"] is None, r["der"] or 0))
    return out


def feed(files: list[AudioFile], limit: int = 20) -> list[dict[str, Any]]:
    stems = {f.id: f.stem for f in files}
    segs = [s for f in files for s in f.segments if s.status == SegmentStatus.confirmed and s.edited_at is not None]
    segs.sort(key=lambda s: s.edited_at, reverse=True)
    return [{"id": s.id, "file_id": s.audio_file_id, "file": stems[s.audio_file_id], "start": s.start, "end": s.end,
             "speaker": s.speaker, "text": s.text, "confirmed_at": s.edited_at.isoformat()} for s in segs[:limit]]


def live(session: Session, ds: Dataset, normaliser: str | None = None, collar: float | None = None) -> dict[str, Any]:
    files = usable_files(session, ds.id)
    collar = der.DEFAULT_COLLAR if collar is None else collar
    return {"dataset": {"id": ds.id, "name": ds.name}, "asr": asr_standings(session, ds, files, normaliser),
            "cpwer": cp_card(session, ds, files, normaliser), "der": der_card(session, files, collar),
            "kws": {"available": False}, "feed": feed(files)}
