"""Add design category and picture, and carvings on furniture parts (3D Lab)

Revision ID: b5e8d2f0c3a7
Revises: a4d7c1e9b2f6
Create Date: 2026-11-04 18:00:00.000000

Additive: designs.category (existing rows stay 'general'), designs.image_key /
image_type / image_sha256 (unique, so one picture is stored once), and table
furniture_carvings (one row per placement of a design on a part face).
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "b5e8d2f0c3a7"
down_revision: Union[str, Sequence[str], None] = "a4d7c1e9b2f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "designs",
        sa.Column("category", sa.String(), nullable=False, server_default="general"),
    )
    op.add_column("designs", sa.Column("image_key", sa.String(), nullable=True))
    op.add_column("designs", sa.Column("image_type", sa.String(), nullable=True))
    op.add_column("designs", sa.Column("image_sha256", sa.String(64), nullable=True))
    op.create_unique_constraint("uq_designs_image_sha256", "designs", ["image_sha256"])
    op.create_table(
        "furniture_carvings",
        sa.Column(
            "id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False
        ),
        sa.Column("item_id", sa.UUID(), nullable=False),
        sa.Column("part_id", sa.UUID(), nullable=True),
        sa.Column("design_id", sa.UUID(), nullable=False),
        sa.Column("face", sa.String(), nullable=False),
        sa.Column("margin_mm", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("mirror", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("sort", sa.Integer(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(["item_id"], ["items.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["part_id"], ["furniture_parts.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["design_id"], ["designs.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_furniture_carvings_item_id", "furniture_carvings", ["item_id"])
    op.create_index(
        "ix_furniture_carvings_design_id", "furniture_carvings", ["design_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_furniture_carvings_design_id", table_name="furniture_carvings")
    op.drop_index("ix_furniture_carvings_item_id", table_name="furniture_carvings")
    op.drop_table("furniture_carvings")
    op.drop_constraint("uq_designs_image_sha256", "designs", type_="unique")
    op.drop_column("designs", "image_sha256")
    op.drop_column("designs", "image_type")
    op.drop_column("designs", "image_key")
    op.drop_column("designs", "category")
