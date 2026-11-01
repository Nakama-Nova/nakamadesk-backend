"""customer accounts for the website: accounts and one-time email codes

Additive only: two new tables, nothing existing changes.

Revision ID: a5b6c7d8e9f0
Revises: f4a5b6c7d8e9
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "a5b6c7d8e9f0"
down_revision: Union[str, Sequence[str], None] = "f4a5b6c7d8e9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "customer_accounts",
        sa.Column(
            "id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False
        ),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("email_verified_at", sa.DateTime(), nullable=True),
        sa.Column("name", sa.String(), nullable=True),
        sa.Column("phone", sa.String(), nullable=True),
        sa.Column("customer_id", sa.UUID(), nullable=True),
        sa.Column("terms_accepted_at", sa.DateTime(), nullable=True),
        sa.Column(
            "marketing_consent",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
        sa.Column(
            "token_version", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("last_login_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email"),
    )
    op.create_table(
        "login_codes",
        sa.Column(
            "id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False
        ),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("code_hash", sa.String(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column(
            "attempts", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column("used_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_login_codes_email_created", "login_codes", ["email", "created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_login_codes_email_created", table_name="login_codes")
    op.drop_table("login_codes")
    op.drop_table("customer_accounts")
