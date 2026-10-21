"""Dataset uid and reference_export (SPLIT_PLAN.md §4.2; CONTRACTS.md §2 source.dataset_uid)

Revision ID: label_0002
Revises: label_0001
Create Date: 2026-10-21 21:30:00
"""

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "label_0002"
down_revision: str | None = "label_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("dataset") as batch:
        batch.add_column(sa.Column("uid", sa.String(length=32), nullable=True))
    conn = op.get_bind()
    for (did,) in conn.execute(sa.text("SELECT id FROM dataset WHERE uid IS NULL")).fetchall():
        conn.execute(sa.text("UPDATE dataset SET uid = :uid WHERE id = :id"), {"uid": uuid.uuid4().hex, "id": did})
    with op.batch_alter_table("dataset") as batch:
        batch.alter_column("uid", existing_type=sa.String(length=32), nullable=False)
        batch.create_unique_constraint("uq_dataset_uid", ["uid"])

    op.create_table(
        "reference_export",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("dataset_id", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("scope", sa.String(length=16), nullable=False),
        sa.Column("tags", sa.JSON(), nullable=False),
        sa.Column("include_audio", sa.String(length=8), nullable=False),
        sa.Column("include_outputs", sa.Boolean(), nullable=False),
        sa.Column("destination", sa.String(length=8), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("counts", sa.JSON(), nullable=False),
        sa.Column("manifest", sa.JSON(), nullable=False),
        sa.Column("path", sa.String(length=1000), nullable=True),
        sa.Column("bench_url", sa.String(length=500), nullable=True),
        sa.Column("bench_import_id", sa.Integer(), nullable=True),
        sa.Column("bench_dataset_id", sa.Integer(), nullable=True),
        sa.Column("bench_preview", sa.JSON(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("job_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["dataset_id"], ["dataset.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["job_id"], ["job.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("reference_export")
    with op.batch_alter_table("dataset") as batch:
        batch.drop_constraint("uq_dataset_uid", type_="unique")
        batch.drop_column("uid")
