"""Models come from the model catalog; outputs are keyed on its build key

Registration and verification moved to the platform's model catalog. The `model` table keeps one row per
catalog model Label has used (runs and outputs refer to model ids): `registry_name` becomes `catalog_id`
(the platform service name, which is the catalog's model id), the protocol is kept from the old registry
snapshot, and the registration columns (parent, check report, registry snapshot) are dropped. The
capability fields are refreshed from the catalog on next use.

`model_output` gains `build_key` (part of its unique key), `params`, and `source_app` / `source_id` for
outputs copied from another app. Existing rows keep their ids, so segments' `meta.members` still resolve,
but their build key is NULL and they never match a new lookup (a new build key means new outputs).

Revision ID: label_0004
Revises: label_0003
Create Date: 2026-10-22 16:00:00
"""

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "label_0004"
down_revision: str | None = "label_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _json(raw):
    return json.loads(raw) if isinstance(raw, str) else dict(raw or {})


def upgrade() -> None:
    conn = op.get_bind()
    op.create_table(
        "model_new",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("catalog_id", sa.String(length=200), nullable=False),
        sa.Column("display_name", sa.String(length=200), nullable=False),
        sa.Column("tags", sa.JSON(), nullable=False),
        sa.Column("capabilities", sa.JSON(), nullable=False),
        sa.Column("timing_level", sa.String(length=16), nullable=True),
        sa.Column("contract", sa.String(length=32), nullable=True),
        sa.Column("api", sa.String(length=64), nullable=True),
        sa.Column("build_key", sa.String(length=16), nullable=True),
        sa.Column("record", sa.JSON(), nullable=False),
        sa.Column("synced_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("catalog_id"),
    )
    rows = conn.execute(sa.text("SELECT id, registry_name, display_name, tags, capabilities, timing_level, "
                                "registry_snapshot, created_at FROM model")).all()
    for mid, name, display, tags, caps, timing, snap, created in rows:
        snap = _json(snap)
        conn.execute(sa.text(
            "INSERT INTO model_new (id, catalog_id, display_name, tags, capabilities, timing_level, contract, api, "
            "build_key, record, synced_at, created_at) VALUES (:id, :cid, :dn, :tags, :caps, :tl, :c, :a, NULL, "
            "'{}', NULL, :created)"),
            {"id": mid, "cid": name, "dn": display, "tags": tags if isinstance(tags, str) else json.dumps(tags or []),
             "caps": caps if isinstance(caps, str) else json.dumps(caps or []), "tl": timing,
             "c": snap.get("contract"), "a": snap.get("api"), "created": created})
    op.drop_table("model")
    op.rename_table("model_new", "model")

    op.create_table(
        "model_output_new",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("model_name", sa.String(length=200), nullable=False),
        sa.Column("audio_sha256", sa.String(length=64), nullable=False),
        sa.Column("span_start", sa.Float(), nullable=False),
        sa.Column("span_end", sa.Float(), nullable=False),
        sa.Column("params_hash", sa.String(length=16), nullable=False),
        sa.Column("build_key", sa.String(length=16), nullable=True),
        sa.Column("params", sa.JSON(), nullable=True),
        sa.Column("output", sa.JSON(), nullable=False),
        sa.Column("latency_s", sa.Float(), nullable=False),
        sa.Column("image", sa.String(length=300), nullable=True),
        sa.Column("source_app", sa.String(length=32), nullable=True),
        sa.Column("source_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("model_name", "audio_sha256", "span_start", "span_end", "params_hash", "build_key"),
    )
    conn.execute(sa.text(
        "INSERT INTO model_output_new (id, model_name, audio_sha256, span_start, span_end, params_hash, build_key, "
        "params, output, latency_s, image, source_app, source_id, created_at) "
        "SELECT id, model_name, audio_sha256, span_start, span_end, params_hash, NULL, NULL, output, latency_s, "
        "image, NULL, NULL, created_at FROM model_output"))
    op.drop_table("model_output")
    op.rename_table("model_output_new", "model_output")
    op.create_index("ix_output_lookup", "model_output", ["model_name", "build_key", "params_hash", "audio_sha256"])

    # `check` jobs are gone with the compatibility check: finished ones are history, unfinished ones cannot run.
    conn.execute(sa.text("UPDATE job SET status = 'failed', error = 'compatibility checks moved to the model "
                         "catalog' WHERE type = 'check' AND status IN ('queued', 'running', 'blocked')"))


def downgrade() -> None:
    """Back to registry names. Capabilities stay; registration data (check report, parent) is gone. Of outputs
    that differ only by build, the newest is kept."""
    conn = op.get_bind()
    op.create_table(
        "model_old",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("registry_name", sa.String(length=200), nullable=False),
        sa.Column("display_name", sa.String(length=200), nullable=False),
        sa.Column("tags", sa.JSON(), nullable=False),
        sa.Column("parent_model_id", sa.Integer(), nullable=True),
        sa.Column("capabilities", sa.JSON(), nullable=False),
        sa.Column("timing_level", sa.String(length=16), nullable=True),
        sa.Column("check_report", sa.JSON(), nullable=False),
        sa.Column("registry_snapshot", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["parent_model_id"], ["model.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("registry_name"),
    )
    for mid, cid, display, tags, caps, timing, contract, api, created in conn.execute(sa.text(
            "SELECT id, catalog_id, display_name, tags, capabilities, timing_level, contract, api, created_at "
            "FROM model")).all():
        conn.execute(sa.text(
            "INSERT INTO model_old (id, registry_name, display_name, tags, parent_model_id, capabilities, timing_level, "
            "check_report, registry_snapshot, created_at) VALUES (:id, :n, :dn, :tags, NULL, :caps, :tl, '{}', :snap, "
            ":created)"),
            {"id": mid, "n": cid, "dn": display, "tags": tags if isinstance(tags, str) else json.dumps(tags or []),
             "caps": caps if isinstance(caps, str) else json.dumps(caps or []), "tl": timing,
             "snap": json.dumps({"name": cid, "contract": contract, "api": api}), "created": created})
    op.drop_table("model")
    op.rename_table("model_old", "model")

    op.drop_index("ix_output_lookup", table_name="model_output")
    op.create_table(
        "model_output_old",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("model_name", sa.String(length=200), nullable=False),
        sa.Column("audio_sha256", sa.String(length=64), nullable=False),
        sa.Column("span_start", sa.Float(), nullable=False),
        sa.Column("span_end", sa.Float(), nullable=False),
        sa.Column("params_hash", sa.String(length=16), nullable=False),
        sa.Column("output", sa.JSON(), nullable=False),
        sa.Column("latency_s", sa.Float(), nullable=False),
        sa.Column("image", sa.String(length=300), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("model_name", "audio_sha256", "span_start", "span_end", "params_hash"),
    )
    conn.execute(sa.text(
        "INSERT INTO model_output_old (id, model_name, audio_sha256, span_start, span_end, params_hash, output, "
        "latency_s, image, created_at) SELECT id, model_name, audio_sha256, span_start, span_end, params_hash, output, "
        "latency_s, image, created_at FROM model_output WHERE id IN (SELECT MAX(id) FROM model_output GROUP BY "
        "model_name, audio_sha256, span_start, span_end, params_hash)"))
    op.drop_table("model_output")
    op.rename_table("model_output_old", "model_output")
