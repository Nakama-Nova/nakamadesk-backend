"""add_raw_material_price_history

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-03-21 11:12:01.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "b2c3d4e5f6a7"
down_revision: Union[str, Sequence[str], None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create raw_material_price_history table."""
    op.create_table(
        "raw_material_price_history",
        sa.Column(
            "id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False
        ),
        sa.Column("material_id", sa.UUID(), nullable=False),
        sa.Column("price", sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column("source", sa.String(), nullable=True),
        sa.Column("recorded_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["material_id"],
            ["raw_materials.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_raw_material_price_history_material_id"),
        "raw_material_price_history",
        ["material_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_raw_material_price_history_recorded_at"),
        "raw_material_price_history",
        ["recorded_at"],
        unique=False,
    )


def downgrade() -> None:
    """Drop raw_material_price_history table."""
    op.drop_index(
        op.f("ix_raw_material_price_history_recorded_at"),
        table_name="raw_material_price_history",
    )
    op.drop_index(
        op.f("ix_raw_material_price_history_material_id"),
        table_name="raw_material_price_history",
    )
    op.drop_table("raw_material_price_history")
