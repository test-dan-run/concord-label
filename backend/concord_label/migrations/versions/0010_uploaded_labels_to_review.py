"""Uploaded labels wait for a person instead of counting as confirmed

A label uploaded with a dataset used to be confirmed at ingest. It now starts in review with a copy in
`meta.uploaded`, so a pseudo-label can sit beside it. This moves the labels nobody has touched to the same state. It
leaves a dataset that the Dataset Catalog or Concord Bench knows about, because its segments were published as
references, and it leaves segments that came from a label package.

Revision ID: label_0010
Revises: label_0009
Create Date: 2026-11-03 18:00:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "label_0010"
down_revision: str | None = "label_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE segment
        SET status = 'pseudo',
            meta = json_set(COALESCE(meta, '{}'), '$.uploaded', json_object('text', text, 'speaker', speaker))
        WHERE status = 'confirmed' AND source_run_id IS NULL AND edited_at IS NULL
          AND json_type(meta, '$.imported_from') IS NULL
          AND audio_file_id IN (SELECT a.id FROM audio_file a JOIN dataset d ON d.id = a.dataset_id
                                WHERE d.catalog_dataset_id IS NULL
                                  AND d.id NOT IN (SELECT dataset_id FROM reference_export))
        """
    )


def downgrade() -> None:
    # Labels nobody has checked go back to confirmed, as they were uploaded, and lose their pseudo-labels.
    op.execute(
        """
        UPDATE segment
        SET status = 'confirmed', text = json_extract(meta, '$.uploaded.text'), confidence = NULL,
            source_run_id = NULL, meta = '{}'
        WHERE json_type(meta, '$.uploaded') IS NOT NULL AND status != 'confirmed' AND edited_at IS NULL
        """
    )
    op.execute("UPDATE segment SET meta = json_remove(meta, '$.uploaded') WHERE json_type(meta, '$.uploaded') IS NOT NULL")
