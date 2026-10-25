"""Undo and redo in Annotate: the history of a person's edits to each file's segments

`segment_edit` holds one row per edit, with every column of the segments it changed before and after.

Revision ID: label_0008
Revises: label_0007
Create Date: 2026-10-25 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "label_0008"
down_revision: str | None = "label_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "segment_edit",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("audio_file_id", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(length=40), nullable=False),
        sa.Column("before", sa.JSON(), nullable=False),
        sa.Column("after", sa.JSON(), nullable=False),
        sa.Column("undone", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["audio_file_id"], ["audio_file.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_segment_edit_file", "segment_edit", ["audio_file_id", "id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_segment_edit_file", table_name="segment_edit")
    op.drop_table("segment_edit")
