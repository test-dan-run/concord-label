"""How a dataset is labelled: one transcript per clip, or speaker turns

`dataset.labelling` picks Annotate's view and whether pseudo-labelling uses a segmenter. A dataset whose files have
at most one segment each, none of them with a speaker, starts as `clips`; the rest start as `turns`.

Revision ID: label_0009
Revises: label_0008
Create Date: 2026-11-03 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "label_0009"
down_revision: str | None = "label_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("dataset") as batch:
        batch.add_column(sa.Column("labelling", sa.String(length=16), nullable=True))
    op.execute("UPDATE dataset SET labelling = 'turns'")
    op.execute(
        """
        UPDATE dataset SET labelling = 'clips'
        WHERE id IN (SELECT a.dataset_id FROM audio_file a JOIN segment s ON s.audio_file_id = a.id)
          AND id NOT IN (SELECT a.dataset_id FROM audio_file a JOIN segment s ON s.audio_file_id = a.id
                         WHERE s.speaker IS NOT NULL)
          AND id NOT IN (SELECT a.dataset_id FROM audio_file a JOIN segment s ON s.audio_file_id = a.id
                         GROUP BY a.id HAVING COUNT(s.id) > 1)
        """
    )


def downgrade() -> None:
    with op.batch_alter_table("dataset") as batch:
        batch.drop_column("labelling")
