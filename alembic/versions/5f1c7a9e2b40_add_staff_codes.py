"""staff set their own password by emailed code

Additive only: one new table; nothing existing changes.

Revision ID: 5f1c7a9e2b40
Revises: a1c2d3e4f5a6
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "5f1c7a9e2b40"
down_revision: Union[str, Sequence[str], None] = "a1c2d3e4f5a6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "staff_codes",
        sa.Column(
            "id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False
        ),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("purpose", sa.String(), nullable=False),
        sa.Column("code_hash", sa.String(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column(
            "attempts", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column("used_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_staff_codes_user_created", "staff_codes", ["user_id", "created_at"]
    )
    # Supabase exposes the public schema through its REST API: keep this table
    # closed to it (this app talks to Postgres directly).
    op.execute("ALTER TABLE staff_codes ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.drop_index("ix_staff_codes_user_created", table_name="staff_codes")
    op.drop_table("staff_codes")
