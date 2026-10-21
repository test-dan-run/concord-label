"""Alembic revisions: 0002 backfills dataset uids, and the head schema matches the ORM models."""

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
        command.upgrade(cfg, "0001")
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
