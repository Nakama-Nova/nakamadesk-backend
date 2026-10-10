"""Add sync_tombstones

Revision ID: f3a4b5c6d7e8
Revises: e2f3a4b5c6d7
Create Date: 2026-10-10 10:00:00.000000

Adds an append-only table recording deleted synced rows so pull_sync can
tell other clients to drop them. Additive only; no existing table changes.
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "f3a4b5c6d7e8"
down_revision: Union[str, Sequence[str], None] = "e2f3a4b5c6d7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "sync_tombstones",
        sa.Column(
            "id",
            sa.UUID(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("entity", sa.String(), nullable=False),
        sa.Column("record_id", sa.UUID(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_sync_tombstones_record_id", "sync_tombstones", ["record_id"])
    op.create_index("ix_sync_tombstones_deleted_at", "sync_tombstones", ["deleted_at"])


def downgrade() -> None:
    op.drop_index("ix_sync_tombstones_deleted_at", table_name="sync_tombstones")
    op.drop_index("ix_sync_tombstones_record_id", table_name="sync_tombstones")
    op.drop_table("sync_tombstones")
