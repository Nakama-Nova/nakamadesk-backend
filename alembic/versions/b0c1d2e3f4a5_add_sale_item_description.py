"""Add sale_items.description

Revision ID: b0c1d2e3f4a5
Revises: a9b0c1d2e3f4
Create Date: 2026-10-30 20:00:00.000000

Additive: a bill line for a custom piece (no catalogue product) needs its own words.
Existing lines keep using the product name.
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "b0c1d2e3f4a5"
down_revision: Union[str, Sequence[str], None] = "a9b0c1d2e3f4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("sale_items", sa.Column("description", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("sale_items", "description")
