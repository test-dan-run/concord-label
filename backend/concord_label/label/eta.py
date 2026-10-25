"""How long a label run has left: the job's `eta`, which the pages show as "about 12 min left".

Each stage of a run (label/pseudo.py) runs one model over some audio. A stage takes its audio times the
model's real-time factor (processing seconds per audio second), plus the model's cold start when it isn't
running. While a stage runs, its factor is what it measures, once a few seconds of inference are done.
Before that, the factor comes from the model's saved outputs (their latency over their audio), and an ASR
member that has none borrows the factor another member measured in this run. A stage whose outputs are all
saved costs nothing. Time spent waiting in the catalog's queue can't be known, so it is left out.

The estimate goes to the job's progress file at most once a second; `counted_down` takes off the time since
it was written, while a stage is running inference.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..core.models import AudioFile, ModelOutput

MIN_MEASURED_S = 5.0  # seconds of inference before a stage's own rate replaces the saved one
REPORT_EVERY_S = 1.0
HISTORY = 500  # saved outputs a model's rate is taken from, newest first


def saved_rtf(session: Session, model_name: str) -> float | None:
    """A model's real-time factor from its newest saved outputs; None without any."""
    durations = (select(AudioFile.sha256, func.max(AudioFile.duration).label("duration"))
                 .group_by(AudioFile.sha256).subquery())
    rows = session.execute(
        select(ModelOutput.latency_s, ModelOutput.span_start, ModelOutput.span_end, durations.c.duration)
        .outerjoin(durations, durations.c.sha256 == ModelOutput.audio_sha256)
        .where(ModelOutput.model_name == model_name, ModelOutput.latency_s > 0)
        .order_by(ModelOutput.id.desc()).limit(HISTORY)).all()
    spent = audio = 0.0
    for latency, start, end, duration in rows:
        seconds = (duration or 0.0) if end < 0 else end - start
        if seconds > 0:
            spent += latency
            audio += seconds
    return spent / audio if audio else None


@dataclass
class Stage:
    model: str
    kind: str  # seg | asr
    audio_s: float  # audio left to run; an estimate until the stage starts
    rtf: float | None = None  # from saved outputs
    cold_s: float = 0.0  # cold start, while the model isn't running
    keys: int | None = None  # calls to make, once the stage starts
    done_keys: int = 0
    done_audio_s: float = 0.0
    started: float | None = None  # when inference began (the lease was active)
    finished: bool = False


class Eta:
    def __init__(self, stages: list[Stage], report: Callable[[dict[str, Any]], None] | None = None,
                 clock: Callable[[], float] = time.monotonic, wall: Callable[[], float] = time.time):
        self.stages = stages
        self._report = report
        self._clock = clock
        self._wall = wall
        self._last = float("-inf")

    def _stage(self, model: str) -> Stage:
        return next(s for s in self.stages if s.model == model)

    def order(self, models: list[str]) -> None:
        """Run the ASR stages in this order (the catalog's plan)."""
        asr = sorted((s for s in self.stages if s.kind == "asr"), key=lambda s: models.index(s.model))
        self.stages = [s for s in self.stages if s.kind != "asr"] + asr

    def asr_audio(self, audio_s: float) -> None:
        """The cuts are known: the audio every ASR stage that has work left runs over."""
        for s in self.stages:
            if s.kind == "asr" and s.started is None and s.audio_s > 0:
                s.audio_s = audio_s

    def start(self, model: str, keys: int, audio_s: float) -> None:
        """The stage's lease is active and it has `keys` calls over `audio_s` seconds to make."""
        s = self._stage(model)
        s.keys, s.audio_s, s.cold_s, s.started = keys, audio_s, 0.0, self._clock()
        self.report(force=True)

    def advance(self, model: str, audio_s: float) -> None:
        s = self._stage(model)
        s.done_keys += 1
        s.done_audio_s += audio_s
        self.report()

    def finish(self, model: str) -> None:
        self._stage(model).finished = True
        self.report(force=True)

    def _measured(self, s: Stage) -> float | None:
        if s.started is None or s.done_audio_s <= 0:
            return None
        return (self._clock() - s.started) / s.done_audio_s

    def _rate(self, s: Stage) -> float | None:
        measured = self._measured(s)
        if measured is not None and (s.rtf is None or self._clock() - (s.started or 0.0) >= MIN_MEASURED_S):
            return measured
        if s.rtf is not None:
            return s.rtf
        if s.kind == "asr":
            return next((r for o in self.stages if o.kind == "asr" and (r := self._measured(o)) is not None), None)
        return None

    def remaining(self) -> float | None:
        """Seconds left in every unfinished stage; None while a stage with work has no rate yet."""
        total = 0.0
        for s in self.stages:
            left = max(0.0, s.audio_s - s.done_audio_s)
            if s.finished or left <= 0:
                continue
            rate = self._rate(s)
            if rate is None:
                return None
            total += left * rate + s.cold_s
        return total

    def to_json(self) -> dict[str, Any]:
        i, cur = next(((i, s) for i, s in enumerate(self.stages) if not s.finished), (len(self.stages), None))
        remaining = self.remaining()
        return {"remaining_s": None if remaining is None else round(remaining, 1), "at": self._wall(),
                "active": cur is not None and cur.started is not None, "stage": min(i + 1, len(self.stages)),
                "stages": len(self.stages), "model": cur.model if cur else None,
                "unit": None if cur is None else ("files" if cur.kind == "seg" else "cuts"),
                "done": cur.done_keys if cur else None, "total": cur.keys if cur else None, "fusing": cur is None}

    def report(self, force: bool = False) -> None:
        if self._report is None or (not force and self._clock() - self._last < REPORT_EVERY_S):
            return
        self._last = self._clock()
        self._report(self.to_json())


def counted_down(eta: dict[str, Any] | None, status: str) -> dict[str, Any] | None:
    """The job's estimate as written, less the time since, while a stage is running inference."""
    if not eta:
        return None
    out = dict(eta)
    if status == "running" and out.get("active") and out.get("remaining_s") is not None:
        out["remaining_s"] = max(0.0, out["remaining_s"] - max(0.0, time.time() - (out.get("at") or time.time())))
    return out
