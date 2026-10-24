"""The Dataset Catalog: catalog dataset ids on datasets and on publishes

`dataset.catalog_dataset_id` and `dataset.catalog_version` name the Dataset Catalog dataset a dataset is published
as or was made from, and the version it is based on. A publish is a `reference_export` row with destination
`catalog`, and `reference_export.catalog_dataset_id` names its catalog dataset (DATASET_CATALOG_PLAN.md §8).

Revision ID: label_0007
Revises: label_0006
Create Date: 2026-10-24 18:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "label_0007"
down_revision: str | None = "label_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("dataset") as batch:
        batch.add_column(sa.Column("catalog_dataset_id", sa.String(length=32), nullable=True))
        batch.add_column(sa.Column("catalog_version", sa.Integer(), nullable=True))
        batch.create_index("ix_dataset_catalog_dataset_id", ["catalog_dataset_id"], unique=False)
    with op.batch_alter_table("reference_export") as batch:
        batch.add_column(sa.Column("catalog_dataset_id", sa.String(length=32), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("reference_export") as batch:
        batch.drop_column("catalog_dataset_id")
    with op.batch_alter_table("dataset") as batch:
        batch.drop_index("ix_dataset_catalog_dataset_id")
        batch.drop_column("catalog_version")
        batch.drop_column("catalog_dataset_id")
