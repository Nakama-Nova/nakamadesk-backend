"""stock held for confirmed orders

Additive only: one new table, nothing existing changes.

Revision ID: c7d8e9f0a1b2
Revises: b6c7d8e9f0a1
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "c7d8e9f0a1b2"
down_revision: Union[str, Sequence[str], None] = "b6c7d8e9f0a1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "stock_reservations",
        sa.Column(
            "id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False
        ),
        sa.Column("order_id", sa.UUID(), nullable=False),
        sa.Column("item_id", sa.UUID(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("released_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["item_id"], ["items.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "order_id", "item_id", name="uq_stock_reservations_order_item"
        ),
    )
    op.create_index(
        "ix_stock_reservations_order_id", "stock_reservations", ["order_id"]
    )
    op.create_index("ix_stock_reservations_item_id", "stock_reservations", ["item_id"])


def downgrade() -> None:
    op.drop_index("ix_stock_reservations_item_id", table_name="stock_reservations")
    op.drop_index("ix_stock_reservations_order_id", table_name="stock_reservations")
    op.drop_table("stock_reservations")
