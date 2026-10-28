"""Add item type (furniture or sculpture) and SKU counters

Revision ID: d6e7f8a9b0c1
Revises: c5d6e7f8a9b0
Create Date: 2026-10-28 12:00:00.000000

Additive: items.item_type (every existing item becomes 'furniture') and the
sku_counters table that hands out FUR-0001, SCU-0001 and so on.
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "d6e7f8a9b0c1"
down_revision: Union[str, Sequence[str], None] = "c5d6e7f8a9b0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "items",
        sa.Column(
            "item_type", sa.String(), nullable=False, server_default="furniture"
        ),
    )
    op.create_index("ix_items_item_type", "items", ["item_type"])
    op.create_table(
        "sku_counters",
        sa.Column("prefix", sa.String(), primary_key=True),
        sa.Column("last_no", sa.Integer(), nullable=False, server_default="0"),
    )
    # Supabase exposes the public schema through its REST API: keep this closed to it.
    op.execute("ALTER TABLE sku_counters ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.drop_table("sku_counters")
    op.drop_index("ix_items_item_type", table_name="items")
    op.drop_column("items", "item_type")
