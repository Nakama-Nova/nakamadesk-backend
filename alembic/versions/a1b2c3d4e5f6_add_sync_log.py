"""add_sync_log

Revision ID: a1b2c3d4e5f6
Revises: 540272dec2c4
Create Date: 2026-03-21 11:12:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, Sequence[str], None] = "540272dec2c4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create sync_logs table."""
    op.create_table(
        "sync_logs",
        sa.Column(
            "id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False
        ),
        sa.Column("client_id", sa.String(), nullable=False),
        sa.Column("entity", sa.String(), nullable=False),
        sa.Column("record_id", sa.UUID(), nullable=False),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("error_message", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_sync_logs_client_id"), "sync_logs", ["client_id"], unique=True
    )
    op.create_index(op.f("ix_sync_logs_entity"), "sync_logs", ["entity"], unique=False)
    op.create_index(
        op.f("ix_sync_logs_record_id"), "sync_logs", ["record_id"], unique=False
    )


def downgrade() -> None:
    """Drop sync_logs table."""
    op.drop_index(op.f("ix_sync_logs_record_id"), table_name="sync_logs")
    op.drop_index(op.f("ix_sync_logs_entity"), table_name="sync_logs")
    op.drop_index(op.f("ix_sync_logs_client_id"), table_name="sync_logs")
    op.drop_table("sync_logs")
