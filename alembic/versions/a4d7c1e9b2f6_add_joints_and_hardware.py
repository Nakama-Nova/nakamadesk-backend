"""Add part joints and the furniture hardware list (3D Lab)

Revision ID: a4d7c1e9b2f6
Revises: 7c2e5a8d1f93
Create Date: 2026-11-04 12:00:00.000000

Additive: furniture_parts.joint_type (nullable), table furniture_hardware.
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "a4d7c1e9b2f6"
down_revision: Union[str, Sequence[str], None] = "7c2e5a8d1f93"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "furniture_parts", sa.Column("joint_type", sa.String(), nullable=True)
    )
    op.create_table(
        "furniture_hardware",
        sa.Column(
            "id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False
        ),
        sa.Column("item_id", sa.UUID(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("qty_expr", sa.String(), nullable=False),
        sa.Column("part_id", sa.UUID(), nullable=True),
        sa.Column("assembly_step", sa.Integer(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("sort", sa.Integer(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(["item_id"], ["items.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["part_id"], ["furniture_parts.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_furniture_hardware_item_id", "furniture_hardware", ["item_id"])


def downgrade() -> None:
    op.drop_index("ix_furniture_hardware_item_id", table_name="furniture_hardware")
    op.drop_table("furniture_hardware")
    op.drop_column("furniture_parts", "joint_type")
