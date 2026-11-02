"""phone verification for customer accounts: SMS one-time codes

Additive only: one new nullable column and one new table; nothing existing changes.

Revision ID: a1c2d3e4f5a6
Revises: f0a1b2c3d4e5
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "a1c2d3e4f5a6"
down_revision: Union[str, Sequence[str], None] = "f0a1b2c3d4e5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "customer_accounts",
        sa.Column("phone_verified_at", sa.DateTime(), nullable=True),
    )
    op.create_table(
        "phone_codes",
        sa.Column(
            "id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False
        ),
        sa.Column("account_id", sa.UUID(), nullable=False),
        sa.Column("phone", sa.String(), nullable=False),
        sa.Column("code_hash", sa.String(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column(
            "attempts", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column("used_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["customer_accounts.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_phone_codes_account_id", "phone_codes", ["account_id"])
    op.create_index(
        "ix_phone_codes_phone_created", "phone_codes", ["phone", "created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_phone_codes_phone_created", table_name="phone_codes")
    op.drop_index("ix_phone_codes_account_id", table_name="phone_codes")
    op.drop_table("phone_codes")
    op.drop_column("customer_accounts", "phone_verified_at")
