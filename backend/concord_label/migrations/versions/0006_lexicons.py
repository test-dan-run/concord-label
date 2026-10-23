"""Lexicons: house spellings for words written more than one way

`lexicon` holds a lexicon's draft entries; `lexicon_version` each published (or imported) version.

Revision ID: label_0006
Revises: label_0005
Create Date: 2026-10-24 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "label_0006"
down_revision: str | None = "label_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "lexicon",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("uid", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("draft", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("uid"),
    )
    op.create_table(
        "lexicon_version",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("lexicon_id", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("entries", sa.JSON(), nullable=False),
        sa.Column("imported_from", sa.Integer(), nullable=True),
        sa.Column("published_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["lexicon_id"], ["lexicon.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["imported_from"], ["sync_package.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("lexicon_id", "version"),
    )


def downgrade() -> None:
    op.drop_table("lexicon_version")
    op.drop_table("lexicon")
