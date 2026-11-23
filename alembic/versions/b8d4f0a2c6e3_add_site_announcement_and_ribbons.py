"""Add site_announcements and product_ribbons

Revision ID: b8d4f0a2c6e3
Revises: a7c3e9b1d5f2
Create Date: 2026-11-23 18:00:00.000000

Additive: two tables. The line in the website's top bar, and small labels (new,
bestseller...) on product photos. Nothing existing changes.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "b8d4f0a2c6e3"
down_revision: Union[str, Sequence[str], None] = "a7c3e9b1d5f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "site_announcements",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column("message", sa.String(160), nullable=False),
        sa.Column("link_url", sa.String(300), nullable=True),
        sa.Column("link_label", sa.String(30), nullable=True),
        sa.Column("starts_on", sa.Date(), nullable=True),
        sa.Column("ends_on", sa.Date(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column(
            "updated_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_table(
        "product_ribbons",
        sa.Column(
            "item_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("items.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("ribbon", sa.String(20), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    # Supabase exposes the public schema through its REST API: keep these closed to it.
    op.execute("ALTER TABLE site_announcements ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE product_ribbons ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.drop_table("product_ribbons")
    op.drop_table("site_announcements")
