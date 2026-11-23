"""Add site_hits (anonymous daily visitor counts)

Revision ID: d0f6b2c4e8a5
Revises: c9e5a1b3d7f4
Create Date: 2026-11-23 22:00:00.000000

Additive: one table of daily counts per page or action. No identifier of any visitor is stored.
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "d0f6b2c4e8a5"
down_revision: Union[str, Sequence[str], None] = "c9e5a1b3d7f4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "site_hits",
        sa.Column("day", sa.Date(), primary_key=True),
        sa.Column("kind", sa.String(8), primary_key=True),
        sa.Column("key", sa.String(120), primary_key=True),
        sa.Column("count", sa.Integer(), nullable=False),
    )
    # Supabase exposes the public schema through its REST API: keep this table closed to it.
    op.execute("ALTER TABLE site_hits ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.drop_table("site_hits")
