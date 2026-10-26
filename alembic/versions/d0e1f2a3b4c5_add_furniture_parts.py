"""Add furniture parts and size presets (3D Lab)

Revision ID: d0e1f2a3b4c5
Revises: c9d0e1f2a3b4
Create Date: 2026-10-26 14:00:00.000000

Additive: items.is_furniture, items.size_presets, table furniture_parts.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "d0e1f2a3b4c5"
down_revision: Union[str, Sequence[str], None] = "c9d0e1f2a3b4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "items",
        sa.Column(
            "is_furniture", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )
    op.add_column(
        "items",
        sa.Column(
            "size_presets", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
    )
    op.create_table(
        "furniture_parts",
        sa.Column(
            "id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False
        ),
        sa.Column("item_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("wood_type", sa.String(), nullable=True),
        sa.Column("raw_material_id", sa.UUID(), nullable=True),
        sa.Column("l_expr", sa.String(), nullable=False),
        sa.Column("w_expr", sa.String(), nullable=False),
        sa.Column("t_expr", sa.String(), nullable=False),
        sa.Column("axis", sa.String(), nullable=False, server_default="x"),
        sa.Column("x_expr", sa.String(), nullable=False, server_default="0"),
        sa.Column("y_expr", sa.String(), nullable=False, server_default="0"),
        sa.Column("z_expr", sa.String(), nullable=False, server_default="0"),
        sa.Column("assembly_step", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("joinery_note", sa.Text(), nullable=True),
        sa.Column("model_asset_id", sa.UUID(), nullable=True),
        sa.Column("sort", sa.Integer(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(["item_id"], ["items.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["raw_material_id"], ["raw_materials.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["model_asset_id"], ["product_assets.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_furniture_parts_item_id", "furniture_parts", ["item_id"])


def downgrade() -> None:
    op.drop_index("ix_furniture_parts_item_id", table_name="furniture_parts")
    op.drop_table("furniture_parts")
    op.drop_column("items", "size_presets")
    op.drop_column("items", "is_furniture")
