"""The audio a label run works on: a dataset's usable files, and the cuts pseudo-labelling transcribes.

`prepare_cuts` turns a segmenter's output into ASR cuts: close or short same-speaker segments are merged,
then each cut is padded. Concord Bench runs the same steps for its seg -> ASR pipelines, so a pipeline and
a label run on the same audio ask the ASR model for the same spans.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from .core.models import AudioFile

MERGE_GAP = 0.3  # same-speaker segments closer than this are joined
MIN_SEG = 0.5  # shorter segments are merged into the nearest same-speaker segment...
MERGE_MAX_GAP = 2.0  # ...when it is at most this far away
PAD = 0.1


def usable_files(session: Session, dataset_id: int, include_long: bool = False) -> list[AudioFile]:
    files = session.scalars(select(AudioFile).where(AudioFile.dataset_id == dataset_id)
                            .options(selectinload(AudioFile.segments)).order_by(AudioFile.rel_path)).all()
    return [f for f in files if f.usable or (include_long and f.status == "too_long")]


@dataclass
class Cut:
    start: float
    end: float
    speaker: str | None
    src: list[float]  # the original (unpadded) span


def prepare_cuts(segments: list[list[float]], speakers: list[str] | None, duration: float) -> list[Cut]:
    """Merge close / short same-speaker segments, then pad. Speech-only segments count as one speaker."""
    spk = speakers or ["speech"] * len(segments)
    by: dict[str, list[list[float]]] = {}
    for (s, e), k in zip(segments, spk, strict=True):
        if e > s:
            by.setdefault(k, []).append([max(0.0, s), min(duration, e)])
    cuts: list[Cut] = []
    for k, segs in by.items():
        segs.sort()
        merged: list[list[float]] = []
        for s, e in segs:
            if merged and s - merged[-1][1] < MERGE_GAP:
                merged[-1][1] = max(merged[-1][1], e)
            else:
                merged.append([s, e])
        changed = True
        while changed and len(merged) > 1:
            changed = False
            for i, (s, e) in enumerate(merged):
                if e - s >= MIN_SEG:
                    continue
                gaps = []
                if i > 0:
                    gaps.append((s - merged[i - 1][1], i - 1))
                if i + 1 < len(merged):
                    gaps.append((merged[i + 1][0] - e, i + 1))
                gap, j = min(gaps)
                if gap <= MERGE_MAX_GAP:
                    a, b = sorted((i, j))
                    merged[a] = [merged[a][0], max(merged[a][1], merged[b][1])]
                    del merged[b]
                    changed = True
                    break
        for s, e in merged:
            cuts.append(Cut(round(max(0.0, s - PAD), 3), round(min(duration, e + PAD), 3),
                            k if speakers else None, [s, e]))
    return sorted(cuts, key=lambda c: (c.start, c.end))
