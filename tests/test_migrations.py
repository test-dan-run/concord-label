"""Alembic revisions: 0002 backfills dataset uids, 0004 moves models to the catalog, and the head schema
matches the ORM models."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext


def test_0002_backfills_dataset_uids(tmp_path, monkeypatch):
    from concord_label.core import db
    from concord_label.core.settings import reset_settings

    monkeypatch.setenv("CONCORD_DATA", str(tmp_path / "data"))
    reset_settings()
    db.reset_engines()
    try:
        (tmp_path / "data").mkdir()
        cfg = db.alembic_config()
        command.upgrade(cfg, "label_0001")
        eng = sa.create_engine(db.db_url())
        with eng.begin() as c:
            for name in ("a", "b"):
                c.execute(sa.text("INSERT INTO dataset (name, tags, label_types, mode, status, report, created_at) "
                                  "VALUES (:n, '[]', '[]', 'audio_only', 'ready', '{}', '2026-10-21')"), {"n": name})
        command.upgrade(cfg, "head")
        with eng.connect() as c:
            uids = [r[0] for r in c.execute(sa.text("SELECT uid FROM dataset"))]
            assert len(uids) == 2 and len(set(uids)) == 2 and all(len(u) == 32 for u in uids)
            assert c.execute(sa.text("SELECT COUNT(*) FROM reference_export")).scalar() == 0
        eng.dispose()
    finally:
        db.reset_engines()
        reset_settings()


def test_head_matches_models(data_dir):
    from concord_label.core import db
    from concord_label.core.models import Base

    eng = sa.create_engine(db.db_url())
    with eng.connect() as c:
        diff = compare_metadata(MigrationContext.configure(c, opts={"render_as_batch": True}), Base.metadata)
    eng.dispose()
    assert diff == []


def test_0004_models_from_the_catalog(tmp_path, monkeypatch):
    """registry_name -> catalog_id, protocol kept from the registry snapshot; outputs keep their ids and get
    no build key, so they stay readable but never match a lookup."""
    from concord_label.core import db
    from concord_label.core.settings import reset_settings

    monkeypatch.setenv("CONCORD_DATA", str(tmp_path / "data"))
    reset_settings()
    db.reset_engines()
    try:
        (tmp_path / "data").mkdir()
        cfg = db.alembic_config()
        command.upgrade(cfg, "label_0003")
        eng = sa.create_engine(db.db_url())
        with eng.begin() as c:
            c.execute(sa.text(
                "INSERT INTO model (id, registry_name, display_name, tags, parent_model_id, capabilities, timing_level, "
                "check_report, registry_snapshot, created_at) VALUES (7, 'whisper-large-v3', 'Whisper', '[\"en\"]', "
                "NULL, '[\"asr\"]', 'segment', '{}', :snap, '2026-10-21')"),
                {"snap": '{"contract": "openai", "api": "audio.transcriptions", "image": "vllm:1"}'})
            c.execute(sa.text(
                "INSERT INTO model_output (id, model_name, audio_sha256, span_start, span_end, params_hash, output, "
                "latency_s, image, created_at) VALUES (41, 'whisper-large-v3', 'abc', 0.0, -1.0, '0123456789abcdef', "
                "'{\"text\": \"hi\"}', 0.5, 'vllm:1', '2026-10-21')"))
            c.execute(sa.text("INSERT INTO job (type, payload, status, required_services, attempts, progress, result, "
                              "created_at) VALUES ('check', '{}', 'queued', '[]', 0, 0, '{}', '2026-10-21')"))
        command.upgrade(cfg, "head")
        with eng.connect() as c:
            m = c.execute(sa.text("SELECT id, catalog_id, display_name, capabilities, timing_level, contract, api, "
                                  "build_key FROM model")).one()
            assert tuple(m) == (7, "whisper-large-v3", "Whisper", '["asr"]', "segment", "openai",
                                "audio.transcriptions", None)
            o = c.execute(sa.text("SELECT id, build_key, params, source_app, image FROM model_output")).one()
            assert tuple(o) == (41, None, None, None, "vllm:1")
            assert c.execute(sa.text("SELECT status FROM job WHERE type = 'check'")).scalar() == "failed"
        eng.dispose()
        from concord_label.core.db import SessionLocal
        from concord_label.runs import cache

        with SessionLocal() as s:
            assert cache.outputs_for(s, [41])[41].output == {"text": "hi"}  # still readable by id
            assert cache.local_rows(s, "whisper-large-v3", None, "0123456789abcdef", ["abc"]) == {}
            assert cache.local_rows(s, "whisper-large-v3", "feedfacefeedface", "0123456789abcdef", ["abc"]) == {}
    finally:
        db.reset_engines()
        reset_settings()
