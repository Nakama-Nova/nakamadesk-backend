"""Add public catalogue fields, product_assets, web order fields

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
Create Date: 2026-10-26 10:00:00.000000

Additive only (old desktop clients keep working):
- items: slug, is_published, made_to_order, lead_time_days, short_desc,
  wood_type, dimensions_mm
- product_assets: photos / .glb models per item
- orders: source ('pos'|'web'), delivery_address; created_by becomes nullable
  because a guest web order has no staff user behind it.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "b8c9d0e1f2a3"
down_revision: Union[str, Sequence[str], None] = "a7b8c9d0e1f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("items", sa.Column("slug", sa.String(), nullable=True))
    op.create_index("ix_items_slug", "items", ["slug"], unique=True)
    op.add_column(
        "items",
        sa.Column(
            "is_published", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )
    op.add_column(
        "items",
        sa.Column(
            "made_to_order", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )
    op.add_column("items", sa.Column("lead_time_days", sa.Integer(), nullable=True))
    op.add_column("items", sa.Column("short_desc", sa.Text(), nullable=True))
    op.add_column("items", sa.Column("wood_type", sa.String(), nullable=True))
    op.add_column(
        "items",
        sa.Column(
            "dimensions_mm", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
    )

    op.create_table(
        "product_assets",
        sa.Column(
            "id",
            sa.UUID(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("item_id", sa.UUID(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("storage_key", sa.String(), nullable=False),
        sa.Column("content_type", sa.String(), nullable=False),
        sa.Column("bytes", sa.Integer(), nullable=False),
        sa.Column("sort", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("alt", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["item_id"], ["items.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_product_assets_item_id", "product_assets", ["item_id"])

    op.add_column(
        "orders",
        sa.Column("source", sa.String(), nullable=False, server_default="pos"),
    )
    op.add_column(
        "orders",
        sa.Column(
            "delivery_address", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
    )
    op.alter_column("orders", "created_by", existing_type=sa.UUID(), nullable=True)


def downgrade() -> None:
    # Fails if web orders exist (created_by would be NULL): delete or assign them first.
    op.alter_column("orders", "created_by", existing_type=sa.UUID(), nullable=False)
    op.drop_column("orders", "delivery_address")
    op.drop_column("orders", "source")
    op.drop_index("ix_product_assets_item_id", table_name="product_assets")
    op.drop_table("product_assets")
    for col in (
        "dimensions_mm",
        "wood_type",
        "short_desc",
        "lead_time_days",
        "made_to_order",
        "is_published",
    ):
        op.drop_column("items", col)
    op.drop_index("ix_items_slug", table_name="items")
    op.drop_column("items", "slug")
