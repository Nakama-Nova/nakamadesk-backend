"""saved delivery addresses for website accounts, and the account on an order

Additive only: one new table and one nullable column.

Revision ID: b6c7d8e9f0a1
Revises: a5b6c7d8e9f0
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "b6c7d8e9f0a1"
down_revision: Union[str, Sequence[str], None] = "a5b6c7d8e9f0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "customer_addresses",
        sa.Column(
            "id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False
        ),
        sa.Column("account_id", sa.UUID(), nullable=False),
        sa.Column("label", sa.String(), nullable=True),
        sa.Column("recipient_name", sa.String(), nullable=False),
        sa.Column("phone", sa.String(), nullable=True),
        sa.Column("address", sa.String(), nullable=False),
        sa.Column("town", sa.String(), nullable=False),
        sa.Column("pincode", sa.String(), nullable=False),
        sa.Column(
            "is_default", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["customer_accounts.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_customer_addresses_account_id", "customer_addresses", ["account_id"]
    )
    op.add_column("orders", sa.Column("account_id", sa.UUID(), nullable=True))
    op.create_foreign_key(
        "fk_orders_account_id", "orders", "customer_accounts", ["account_id"], ["id"]
    )
    op.create_index("ix_orders_account_id", "orders", ["account_id"])


def downgrade() -> None:
    op.drop_index("ix_orders_account_id", table_name="orders")
    op.drop_constraint("fk_orders_account_id", "orders", type_="foreignkey")
    op.drop_column("orders", "account_id")
    op.drop_index("ix_customer_addresses_account_id", table_name="customer_addresses")
    op.drop_table("customer_addresses")
