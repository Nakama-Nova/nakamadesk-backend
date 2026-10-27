"""Add login attempts (sign-in throttling)

Revision ID: f2a3b4c5d6e7
Revises: e1f2a3b4c5d6
Create Date: 2026-10-27 16:00:00.000000

Additive: table login_attempts.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "f2a3b4c5d6e7"
down_revision: Union[str, Sequence[str], None] = "e1f2a3b4c5d6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "login_attempts",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column("username", sa.String(), nullable=False),
        sa.Column("ip", sa.String(), nullable=False),
        sa.Column(
            "at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_login_attempts_username_at", "login_attempts", ["username", "at"]
    )
    op.create_index("ix_login_attempts_ip_at", "login_attempts", ["ip", "at"])
    # Supabase exposes the public schema through its REST API: keep this table
    # closed to it (this app talks to Postgres directly).
    op.execute("ALTER TABLE login_attempts ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.drop_index("ix_login_attempts_ip_at", table_name="login_attempts")
    op.drop_index("ix_login_attempts_username_at", table_name="login_attempts")
    op.drop_table("login_attempts")
