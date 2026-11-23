"""Add site_banners

Revision ID: c9e5a1b3d7f4
Revises: b8d4f0a2c6e3
Create Date: 2026-11-23 20:00:00.000000

Additive: one table. The feature banner on the website's home page (headline, picture
or YouTube / Instagram video, button), edited by staff.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "c9e5a1b3d7f4"
down_revision: Union[str, Sequence[str], None] = "b8d4f0a2c6e3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "site_banners",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column("headline", sa.String(80), nullable=False),
        sa.Column("body", sa.String(240), nullable=True),
        sa.Column("media_kind", sa.String(12), nullable=False),
        sa.Column("video_id", sa.String(40), nullable=True),
        sa.Column("video_variant", sa.String(8), nullable=True),
        sa.Column("image_key", sa.String(300), nullable=True),
        sa.Column("image_storage", sa.String(12), nullable=True),
        sa.Column("button_url", sa.String(300), nullable=True),
        sa.Column("button_label", sa.String(30), nullable=True),
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
    # Supabase exposes the public schema through its REST API: keep this table closed to it.
    op.execute("ALTER TABLE site_banners ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.drop_table("site_banners")
