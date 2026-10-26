"""Add product_assets visibility, storage, scan_id

Revision ID: c9d0e1f2a3b4
Revises: b8c9d0e1f2a3
Create Date: 2026-10-26 12:00:00.000000

Additive. New rows default to visibility 'staff' (safe); every file that
exists today is already part of the public catalogue, so it is backfilled
to 'public'.
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "c9d0e1f2a3b4"
down_revision: Union[str, Sequence[str], None] = "b8c9d0e1f2a3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "product_assets",
        sa.Column("visibility", sa.String(), nullable=False, server_default="staff"),
    )
    op.add_column(
        "product_assets",
        sa.Column("storage", sa.String(), nullable=False, server_default="local"),
    )
    op.add_column("product_assets", sa.Column("scan_id", sa.UUID(), nullable=True))
    op.execute("UPDATE product_assets SET visibility = 'public'")


def downgrade() -> None:
    op.drop_column("product_assets", "scan_id")
    op.drop_column("product_assets", "storage")
    op.drop_column("product_assets", "visibility")
