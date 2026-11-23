"""Add customer_comments

Revision ID: a7c3e9b1d5f2
Revises: e4b8c2d6f1a3
Create Date: 2026-11-23 12:00:00.000000

Additive: one table. A customer's comment about their order, held as `pending` until
staff approve it; one per order.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "a7c3e9b1d5f2"
down_revision: Union[str, Sequence[str], None] = "e4b8c2d6f1a3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "customer_comments",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column(
            "order_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("orders.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("display_name", sa.String(60), nullable=False),
        sa.Column("place", sa.String(60), nullable=True),
        sa.Column("piece", sa.String(150), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("decided_at", sa.DateTime(), nullable=True),
        sa.Column(
            "decided_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_customer_comments_status_created",
        "customer_comments",
        ["status", "created_at"],
    )
    # Supabase exposes the public schema through its REST API: keep this table closed to it.
    op.execute("ALTER TABLE customer_comments ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.drop_table("customer_comments")
