"""Concord Label's SQLite schema (PLAN §4, SPLIT_PLAN.md §2.1).

The tables Concord Bench also has (dataset, audio_file, segment, keyword_event, model, model_output, job,
offline_export) started identical to Bench's and may change separately; only CONTRACTS.md must stay
compatible. Label's own: `run` (label runs only), `sync_package` and `reference_export`.

`dataset.uid` names a dataset across apps: it is `source.dataset_uid` in the reference packages sent to
Concord Bench (CONTRACTS.md §2) and in the flags that come back (§3).

Pseudo-labels carry `source_run_id` (a `label` run) and a status from confidence triage; a person's edits
set `edited_at`. Improve can later read segments without Label importing it.

KWS hooks (Q6): `kws` is in the label-type and capability enums and `keyword_event` exists, but nothing
writes them yet.
"""

from __future__ import annotations

import enum
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def new_uid() -> str:
    return uuid.uuid4().hex


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSON, list[str]: JSON, list[Any]: JSON}


class LabelType(enum.StrEnum):
    asr = "asr"
    diar = "diar"
    kws = "kws"


class Capability(enum.StrEnum):
    asr = "asr"
    vad = "vad"
    diar = "diar"
    kws = "kws"


class TimingLevel(enum.StrEnum):
    word = "word"
    segment = "segment"
    text = "text"


class RunTask(enum.StrEnum):
    label = "label"  # ensemble pseudo-labelling; Concord Bench's asr / seg / pipeline runs live in Bench


class FileStatus(enum.StrEnum):
    ok = "ok"
    clipped = "clipped"
    undecodable = "undecodable"
    too_long = "too_long"


class SegmentStatus(enum.StrEnum):
    pseudo = "pseudo"
    auto = "auto"
    confirmed = "confirmed"
    rejected = "rejected"


class JobStatus(enum.StrEnum):
    queued = "queued"
    running = "running"
    done = "done"
    failed = "failed"
    blocked = "blocked"  # the platform could not start a required service


class Dataset(Base):
    __tablename__ = "dataset"
    id: Mapped[int] = mapped_column(primary_key=True)
    uid: Mapped[str] = mapped_column(String(32), default=new_uid, unique=True)
    name: Mapped[str] = mapped_column(String(200))
    language: Mapped[str | None] = mapped_column(String(32))
    description: Mapped[str | None] = mapped_column(Text)
    tags: Mapped[list[str]] = mapped_column(default=list)
    label_types: Mapped[list[str]] = mapped_column(default=list)
    mode: Mapped[str] = mapped_column(String(16), default="annotated")  # annotated | audio_only
    status: Mapped[str] = mapped_column(String(16), default="ingesting")  # ingesting | ready | failed
    # Ingest report: counts, hours, sample-rate histogram, flagged files, annotation matches.
    report: Mapped[dict[str, Any]] = mapped_column(default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    files: Mapped[list[AudioFile]] = relationship(back_populates="dataset", cascade="all, delete-orphan")


class AudioFile(Base):
    __tablename__ = "audio_file"
    __table_args__ = (UniqueConstraint("dataset_id", "rel_path"), Index("ix_audio_sha", "sha256"))
    id: Mapped[int] = mapped_column(primary_key=True)
    dataset_id: Mapped[int] = mapped_column(ForeignKey("dataset.id", ondelete="CASCADE"))
    rel_path: Mapped[str] = mapped_column(String(1000))
    sha256: Mapped[str] = mapped_column(String(64))
    ext: Mapped[str] = mapped_column(String(16))
    duration: Mapped[float | None] = mapped_column(Float)
    orig_sr: Mapped[int | None] = mapped_column(Integer)
    channels: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default=FileStatus.ok)
    # Free-form metadata used by "WER by slice" (e.g. condition, noise) from annotation files.
    meta: Mapped[dict[str, Any]] = mapped_column(default=dict)

    dataset: Mapped[Dataset] = relationship(back_populates="files")
    segments: Mapped[list[Segment]] = relationship(back_populates="audio_file", cascade="all, delete-orphan")

    @property
    def stem(self) -> str:
        name = self.rel_path.rsplit("/", 1)[-1]
        return name.rsplit(".", 1)[0] if "." in name else name

    @property
    def usable(self) -> bool:
        return self.status in (FileStatus.ok, FileStatus.clipped)


class Segment(Base):
    """A reference (or, from Phase 2, pseudo) label. Text-only file-level transcripts are one segment
    spanning the file with no speaker; RTTM turns have a speaker and no text; speaker-attributed
    transcripts have both.

    `uid` identifies the segment across installs (label packages). `edited_at` is set whenever a person
    changes or reviews it; pseudo-labelling never replaces a segment a person has touched. `meta` holds
    what pseudo-labelling knew: the ASR span, each member's cache row and the chosen member."""

    __tablename__ = "segment"
    __table_args__ = (Index("ix_segment_file", "audio_file_id", "start"), Index("ix_segment_uid", "uid"))
    id: Mapped[int] = mapped_column(primary_key=True)
    uid: Mapped[str] = mapped_column(String(32), default=new_uid)
    audio_file_id: Mapped[int] = mapped_column(ForeignKey("audio_file.id", ondelete="CASCADE"))
    start: Mapped[float] = mapped_column(Float)
    end: Mapped[float] = mapped_column(Float)
    speaker: Mapped[str | None] = mapped_column(String(100))
    text: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default=SegmentStatus.confirmed)
    confidence: Mapped[float | None] = mapped_column(Float)
    source_run_id: Mapped[int | None] = mapped_column(ForeignKey("run.id", ondelete="SET NULL"))
    flagged: Mapped[bool] = mapped_column(Boolean, default=False)
    flag_note: Mapped[str | None] = mapped_column(Text)
    edited_at: Mapped[datetime | None] = mapped_column(DateTime)
    meta: Mapped[dict[str, Any]] = mapped_column(default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    audio_file: Mapped[AudioFile] = relationship(back_populates="segments")

    @property
    def reviewed(self) -> bool:
        """Reviewed work: confirmed, or touched by a person. Pseudo-labels never overwrite it."""
        return self.status == SegmentStatus.confirmed or self.edited_at is not None


class KeywordEvent(Base):
    """KWS hook (Q6): created now so KWS needs no migration later. Unused in Phase 1."""

    __tablename__ = "keyword_event"
    id: Mapped[int] = mapped_column(primary_key=True)
    audio_file_id: Mapped[int] = mapped_column(ForeignKey("audio_file.id", ondelete="CASCADE"))
    keyword: Mapped[str] = mapped_column(String(200))
    start: Mapped[float] = mapped_column(Float)
    end: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(16), default=SegmentStatus.confirmed)
    confidence: Mapped[float | None] = mapped_column(Float)
    source_run_id: Mapped[int | None] = mapped_column(ForeignKey("run.id", ondelete="SET NULL"))


class Model(Base):
    __tablename__ = "model"
    id: Mapped[int] = mapped_column(primary_key=True)
    registry_name: Mapped[str] = mapped_column(String(200), unique=True)
    display_name: Mapped[str] = mapped_column(String(200))
    tags: Mapped[list[str]] = mapped_column(default=list)
    parent_model_id: Mapped[int | None] = mapped_column(ForeignKey("model.id", ondelete="SET NULL"))
    capabilities: Mapped[list[str]] = mapped_column(default=list)
    timing_level: Mapped[str | None] = mapped_column(String(16))
    check_report: Mapped[dict[str, Any]] = mapped_column(default=dict)
    registry_snapshot: Mapped[dict[str, Any]] = mapped_column(default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    @property
    def is_asr(self) -> bool:
        return Capability.asr in self.capabilities

    @property
    def is_seg(self) -> bool:
        return Capability.vad in self.capabilities or Capability.diar in self.capabilities

    @property
    def has_speakers(self) -> bool:
        return Capability.diar in self.capabilities


class Run(Base):
    """One label run (pseudo-labelling a dataset). The models it uses are named in `params`."""

    __tablename__ = "run"
    id: Mapped[int] = mapped_column(primary_key=True)
    task: Mapped[str] = mapped_column(String(16), default=RunTask.label)
    dataset_id: Mapped[int] = mapped_column(ForeignKey("dataset.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(String(16), default="queued")  # queued | running | done | failed | blocked
    error: Mapped[str | None] = mapped_column(Text)
    image_tags: Mapped[dict[str, Any]] = mapped_column(default=dict)  # service -> {image, labels}
    params: Mapped[dict[str, Any]] = mapped_column(default=dict)
    # Per-file cuts and the cache rows of each member, plus the run's summary (label/pseudo.py).
    outputs: Mapped[dict[str, Any]] = mapped_column(default=dict)
    rtf_by_stage: Mapped[dict[str, Any]] = mapped_column(default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)

    dataset: Mapped[Dataset] = relationship()


class ModelOutput(Base):
    """The inference cache, keyed on (model, audio, span, params). Label runs read and fill it, and live
    evaluation reads it."""

    __tablename__ = "model_output"
    __table_args__ = (UniqueConstraint("model_name", "audio_sha256", "span_start", "span_end", "params_hash"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    model_name: Mapped[str] = mapped_column(String(200))  # platform service name
    audio_sha256: Mapped[str] = mapped_column(String(64))
    span_start: Mapped[float] = mapped_column(Float, default=0.0)
    span_end: Mapped[float] = mapped_column(Float, default=-1.0)  # -1 = whole file
    params_hash: Mapped[str] = mapped_column(String(16))
    output: Mapped[dict[str, Any]] = mapped_column()
    latency_s: Mapped[float] = mapped_column(Float)
    image: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Job(Base):
    __tablename__ = "job"
    id: Mapped[int] = mapped_column(primary_key=True)
    type: Mapped[str] = mapped_column(String(32))  # ingest | check | label | export
    payload: Mapped[dict[str, Any]] = mapped_column(default=dict)
    status: Mapped[str] = mapped_column(String(16), default=JobStatus.queued)
    required_services: Mapped[list[str]] = mapped_column(default=list)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    message: Mapped[str | None] = mapped_column(String(300))
    result: Mapped[dict[str, Any]] = mapped_column(default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)


class SyncPackage(Base):
    """One label package exported from or imported into this install (HANDOFF §4, Import / export).

    Imports keep the merge outcome, the conflict list and the path of the snapshot taken before the
    merge, so a merge can be undone."""

    __tablename__ = "sync_package"
    id: Mapped[int] = mapped_column(primary_key=True)
    direction: Mapped[str] = mapped_column(String(8))  # export | import
    dataset_id: Mapped[int | None] = mapped_column(ForeignKey("dataset.id", ondelete="SET NULL"))
    # export: built | import: staged | applied | undone
    status: Mapped[str] = mapped_column(String(16))
    scope: Mapped[dict[str, Any]] = mapped_column(default=dict)
    counts: Mapped[dict[str, Any]] = mapped_column(default=dict)
    manifest: Mapped[dict[str, Any]] = mapped_column(default=dict)
    conflicts: Mapped[list[Any]] = mapped_column(default=list)
    path: Mapped[str | None] = mapped_column(String(1000))  # the package zip (export) or staged folder (import)
    snapshot_path: Mapped[str | None] = mapped_column(String(1000))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime)
    undone_at: Mapped[datetime | None] = mapped_column(DateTime)


class ReferenceExport(Base):
    """One reference package sent to Concord Bench, pushed or downloaded (SPLIT_PLAN.md §4.2,
    CONTRACTS.md §2). `version` is the package's `export_version`, given when the package is written."""

    __tablename__ = "reference_export"
    id: Mapped[int] = mapped_column(primary_key=True)
    dataset_id: Mapped[int] = mapped_column(ForeignKey("dataset.id", ondelete="CASCADE"))
    version: Mapped[int | None] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(200))
    scope: Mapped[str] = mapped_column(String(16))  # complete | any
    tags: Mapped[list[str]] = mapped_column(default=list)
    include_audio: Mapped[str] = mapped_column(String(8), default="auto")  # auto | always | never
    include_outputs: Mapped[bool] = mapped_column(Boolean, default=False)
    destination: Mapped[str] = mapped_column(String(8))  # download | push
    status: Mapped[str] = mapped_column(String(16), default="queued")  # queued | running | done | failed
    # files, segments, hours, audio_files, outputs, left_out (not fully reviewed), bytes
    counts: Mapped[dict[str, Any]] = mapped_column(default=dict)
    manifest: Mapped[dict[str, Any]] = mapped_column(default=dict)
    path: Mapped[str | None] = mapped_column(String(1000))  # the zip
    bench_url: Mapped[str | None] = mapped_column(String(500))
    bench_import_id: Mapped[int | None] = mapped_column(Integer)  # Bench's staged reference import
    bench_dataset_id: Mapped[int | None] = mapped_column(Integer)  # set if Bench ever reports it
    bench_preview: Mapped[dict[str, Any]] = mapped_column(default=dict)
    error: Mapped[str | None] = mapped_column(Text)
    job_id: Mapped[int | None] = mapped_column(ForeignKey("job.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)


class OfflineExport(Base):
    """One offline kit written by this install (OFFLINE_EXPORT_PLAN.md §5). The kit is a folder on disk;
    nothing of Concord's own state goes into it."""

    __tablename__ = "offline_export"
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(8))  # full | models
    services: Mapped[list[str]] = mapped_column(default=list)
    concord_image: Mapped[str | None] = mapped_column(String(300))  # full kits only
    dest: Mapped[str] = mapped_column(String(1000))  # the folder the kit is written into
    name: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(16), default="queued")  # queued | running | done | failed
    path: Mapped[str | None] = mapped_column(String(1000))  # the kit folder, once written
    bytes: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)
    job_id: Mapped[int | None] = mapped_column(ForeignKey("job.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
