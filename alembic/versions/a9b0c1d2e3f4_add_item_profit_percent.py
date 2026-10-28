"""Add items.profit_percent

Revision ID: a9b0c1d2e3f4
Revises: f8a9b0c1d2e3
Create Date: 2026-10-28 20:00:00.000000

Additive: when set, an item's selling price follows (buying price + making cost) plus
this profit %. Empty (every existing item) means the price is typed by hand, as before.
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "a9b0c1d2e3f4"
down_revision: Union[str, Sequence[str], None] = "f8a9b0c1d2e3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("items", sa.Column("profit_percent", sa.Numeric(7, 2), nullable=True))


def downgrade() -> None:
    op.drop_column("items", "profit_percent")
