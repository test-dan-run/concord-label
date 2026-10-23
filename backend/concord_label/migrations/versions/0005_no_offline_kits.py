"""Offline kits moved to the model catalog

Concord Label no longer writes offline kits: the `offline_export` table is dropped. Finished `export` jobs
stay as history; unfinished ones cannot run any more and are failed.

Revision ID: label_0005
Revises: label_0004
Create Date: 2026-10-24 09:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "label_0005"
down_revision: str | None = "label_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_table("offline_export")
    op.get_bind().execute(sa.text(
        "UPDATE job SET status = 'failed', error = 'offline kits moved to the model catalog' "
        "WHERE type = 'export' AND status IN ('queued', 'running', 'waiting', 'blocked')"))


def downgrade() -> None:
    """The table comes back empty: the kits written before are folders on disk, not rows."""
    op.create_table(
        "offline_export",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=8), nullable=False),
        sa.Column("services", sa.JSON(), nullable=False),
        sa.Column("concord_image", sa.String(length=300), nullable=True),
        sa.Column("dest", sa.String(length=1000), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("path", sa.String(length=1000), nullable=True),
        sa.Column("bytes", sa.Integer(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("job_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["job_id"], ["job.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
