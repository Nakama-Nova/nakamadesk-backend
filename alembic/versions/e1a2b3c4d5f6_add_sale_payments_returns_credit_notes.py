"""Payments on a bill, returns and credit notes

Revision ID: e1a2b3c4d5f6
Revises: d1e2f3a4b5c6
Create Date: 2026-11-24 18:00:00.000000

Additive only: four new tables (sale_payments, sale_returns, sale_return_items and the
credit_note_counters counter, one row per financial year). Nothing existing changes, so
the migration can run before the new code is live.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "e1a2b3c4d5f6"
down_revision: Union[str, Sequence[str], None] = "d1e2f3a4b5c6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

UUID = postgresql.UUID(as_uuid=True)
NEW_ID = sa.text("gen_random_uuid()")
NOW = sa.text("now()")


def upgrade() -> None:
    op.create_table(
        "credit_note_counters",
        sa.Column("fy_start", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("last_no", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("fy_start"),
    )
    op.create_table(
        "sale_payments",
        sa.Column("id", UUID, server_default=NEW_ID, nullable=False),
        sa.Column("sale_id", UUID, nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("method", sa.String(), server_default="cash", nullable=False),
        sa.Column("reference", sa.String(), nullable=True),
        sa.Column("note", sa.String(), nullable=True),
        sa.Column("paid_on", sa.Date(), nullable=False),
        sa.Column("created_by", UUID, nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False
        ),
        sa.Column("voided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("voided_by", UUID, nullable=True),
        sa.Column("void_reason", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(["sale_id"], ["sales.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["voided_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_sale_payments_sale", "sale_payments", ["sale_id", "paid_on"]
    )
    op.create_table(
        "sale_returns",
        sa.Column("id", UUID, server_default=NEW_ID, nullable=False),
        sa.Column("credit_note_number", sa.String(), nullable=False),
        sa.Column("sale_id", UUID, nullable=False),
        sa.Column("reason", sa.String(), nullable=False),
        sa.Column("refund_method", sa.String(), nullable=False),
        sa.Column("refund_amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("restocked", sa.Boolean(), nullable=False),
        sa.Column("created_by", UUID, nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False
        ),
        sa.ForeignKeyConstraint(["sale_id"], ["sales.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_sale_returns_credit_note_number",
        "sale_returns",
        ["credit_note_number"],
        unique=True,
    )
    op.create_index("ix_sale_returns_sale_id", "sale_returns", ["sale_id"])
    op.create_table(
        "sale_return_items",
        sa.Column("id", UUID, server_default=NEW_ID, nullable=False),
        sa.Column("return_id", UUID, nullable=False),
        sa.Column("sale_item_id", UUID, nullable=False),
        sa.Column("item_id", UUID, nullable=True),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("taxable_value", sa.Numeric(12, 2), nullable=False),
        sa.Column("cgst_amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("sgst_amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.ForeignKeyConstraint(["return_id"], ["sale_returns.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["sale_item_id"], ["sale_items.id"]),
        sa.ForeignKeyConstraint(["item_id"], ["items.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_sale_return_items_return_id", "sale_return_items", ["return_id"])
    op.create_index(
        "ix_sale_return_items_sale_item_id", "sale_return_items", ["sale_item_id"]
    )


def downgrade() -> None:
    op.drop_table("sale_return_items")
    op.drop_table("sale_returns")
    op.drop_table("sale_payments")
    op.drop_table("credit_note_counters")
