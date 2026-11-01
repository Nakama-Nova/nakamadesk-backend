"""budgets for machines and raw materials

Additive only: one new table, nothing existing changes.

Revision ID: e9f0a1b2c3d4
Revises: d8e9f0a1b2c3
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "e9f0a1b2c3d4"
down_revision: Union[str, Sequence[str], None] = "d8e9f0a1b2c3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "budgets",
        sa.Column(
            "id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False
        ),
        sa.Column("category", sa.String(), nullable=False),
        sa.Column("period", sa.String(), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("set_by", sa.UUID(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "category IN ('machines', 'raw_materials')", name="ck_budgets_category"
        ),
        sa.CheckConstraint(
            "period IN ('month', 'financial_year')", name="ck_budgets_period"
        ),
        sa.CheckConstraint("amount >= 0", name="ck_budgets_amount"),
        sa.ForeignKeyConstraint(["set_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "category", "period", "period_start", name="uq_budgets_category_period"
        ),
    )


def downgrade() -> None:
    op.drop_table("budgets")
