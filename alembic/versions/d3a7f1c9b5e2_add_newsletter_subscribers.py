"""Add newsletter subscribers (double opt-in consent record)

Revision ID: d3a7f1c9b5e2
Revises: c6f9e3a1d8b4
Create Date: 2026-11-22 12:00:00.000000

Additive: table newsletter_subscribers.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "d3a7f1c9b5e2"
down_revision: Union[str, Sequence[str], None] = "c6f9e3a1d8b4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "newsletter_subscribers",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column("email", sa.String(), nullable=False, unique=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("source", sa.String(30), nullable=True),
        sa.Column("consent_version", sa.String(40), nullable=False),
        sa.Column("subscribed_at", sa.DateTime(), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(), nullable=True),
        sa.Column("unsubscribed_at", sa.DateTime(), nullable=True),
        sa.Column("last_confirmation_sent_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    # Supabase exposes the public schema through its REST API: keep this table
    # closed to it (this app talks to Postgres directly).
    op.execute("ALTER TABLE newsletter_subscribers ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.drop_table("newsletter_subscribers")
