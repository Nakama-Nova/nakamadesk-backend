"""Add order emails: orders.notify_email and the order_email_log table

Revision ID: e4b8c2d6f1a3
Revises: d3a7f1c9b5e2
Create Date: 2026-11-22 18:00:00.000000

Additive: nullable column orders.notify_email (the address given at website checkout)
and table order_email_log, whose two partial unique indexes keep each email to once per
order (or once per payment).
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "e4b8c2d6f1a3"
down_revision: Union[str, Sequence[str], None] = "d3a7f1c9b5e2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("orders", sa.Column("notify_email", sa.String(), nullable=True))
    op.create_table(
        "order_email_log",
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
        ),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column(
            "payment_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("order_payments.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("to_email", sa.String(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("error", sa.String(200), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "uq_order_email_order_kind",
        "order_email_log",
        ["order_id", "kind"],
        unique=True,
        postgresql_where=sa.text("payment_id IS NULL"),
    )
    op.create_index(
        "uq_order_email_payment_kind",
        "order_email_log",
        ["order_id", "kind", "payment_id"],
        unique=True,
        postgresql_where=sa.text("payment_id IS NOT NULL"),
    )
    op.create_index(
        "ix_order_email_to_kind_created",
        "order_email_log",
        ["to_email", "kind", "created_at"],
    )
    # Supabase exposes the public schema through its REST API: keep this table
    # closed to it (this app talks to Postgres directly).
    op.execute("ALTER TABLE order_email_log ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.drop_table("order_email_log")
    op.drop_column("orders", "notify_email")
