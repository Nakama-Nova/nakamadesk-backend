"""a wrong payment on an order can be voided (kept on record, with the reason)

Additive only: three nullable columns on order_payments. Old code ignores them, so a
deploy can run the migration first without breaking the live app.

Revision ID: 7c2e5a8d1f93
Revises: 6b3d9e1a4c72
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "7c2e5a8d1f93"
down_revision: Union[str, Sequence[str], None] = "6b3d9e1a4c72"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "order_payments",
        sa.Column("voided_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("order_payments", sa.Column("voided_by", sa.UUID(), nullable=True))
    op.add_column(
        "order_payments", sa.Column("void_reason", sa.String(), nullable=True)
    )
    op.create_foreign_key(
        "fk_order_payments_voided_by",
        "order_payments",
        "users",
        ["voided_by"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_order_payments_voided_by", "order_payments", type_="foreignkey"
    )
    op.drop_column("order_payments", "void_reason")
    op.drop_column("order_payments", "voided_by")
    op.drop_column("order_payments", "voided_at")
