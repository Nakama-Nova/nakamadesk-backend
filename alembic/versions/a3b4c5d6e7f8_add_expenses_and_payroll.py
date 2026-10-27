"""Add expenses, payroll pay-outs, extra earnings and worker advances

Revision ID: a3b4c5d6e7f8
Revises: f2a3b4c5d6e7
Create Date: 2026-10-27 18:00:00.000000

Additive: tables expenses, payroll_payments, work_entries, worker_advances;
users.overtime_rate_per_hour; daily_wages.payment_id.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "a3b4c5d6e7f8"
down_revision: Union[str, Sequence[str], None] = "f2a3b4c5d6e7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _id():
    return sa.Column(
        "id",
        postgresql.UUID(as_uuid=True),
        server_default=sa.text("gen_random_uuid()"),
        primary_key=True,
    )


def _created():
    return sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("now()"),
    )


def _user_ref(name: str, nullable: bool, set_null: bool = True):
    return sa.Column(
        name,
        postgresql.UUID(as_uuid=True),
        sa.ForeignKey("users.id", ondelete="SET NULL" if set_null else None),
        nullable=nullable,
    )


def upgrade() -> None:
    op.create_table(
        "expenses",
        _id(),
        sa.Column("spent_on", sa.Date(), nullable=False),
        sa.Column("category", sa.String(), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("payee", sa.String(), nullable=True),
        sa.Column("method", sa.String(), nullable=False, server_default="cash"),
        sa.Column("notes", sa.Text(), nullable=True),
        _user_ref("created_by", True),
        _created(),
        sa.Column("voided_at", sa.DateTime(timezone=True), nullable=True),
        _user_ref("voided_by", True),
        sa.Column("void_reason", sa.String(), nullable=True),
    )
    op.create_index("ix_expenses_spent_on", "expenses", ["spent_on"])

    op.create_table(
        "payroll_payments",
        _id(),
        _user_ref("user_id", False, set_null=False),
        sa.Column("paid_on", sa.Date(), nullable=False),
        sa.Column("through", sa.Date(), nullable=False),
        sa.Column("gross_daily", sa.Numeric(12, 2), nullable=False),
        sa.Column("gross_extras", sa.Numeric(12, 2), nullable=False),
        sa.Column("deductions", sa.Numeric(12, 2), nullable=False),
        sa.Column("net_paid", sa.Numeric(12, 2), nullable=False),
        sa.Column("method", sa.String(), nullable=False, server_default="cash"),
        sa.Column("reference", sa.String(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        _user_ref("paid_by", True),
        _created(),
    )
    op.create_index("ix_payroll_payments_user_id", "payroll_payments", ["user_id"])
    op.create_index(
        "ix_payroll_payments_user_paid", "payroll_payments", ["user_id", "paid_on"]
    )

    payment_ref = sa.ForeignKey("payroll_payments.id", ondelete="SET NULL")

    op.create_table(
        "work_entries",
        _id(),
        _user_ref("user_id", False, set_null=False),
        sa.Column("work_date", sa.Date(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("description", sa.String(), nullable=False),
        sa.Column("quantity", sa.Numeric(10, 2), nullable=False),
        sa.Column("rate", sa.Numeric(10, 2), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column(
            "item_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("items.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("payment_id", postgresql.UUID(as_uuid=True), payment_ref),
        _user_ref("created_by", True),
        _created(),
    )
    op.create_index("ix_work_entries_user_id", "work_entries", ["user_id"])
    op.create_index(
        "ix_work_entries_user_status", "work_entries", ["user_id", "status"]
    )

    op.create_table(
        "worker_advances",
        _id(),
        _user_ref("user_id", False, set_null=False),
        sa.Column("given_on", sa.Date(), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "payment_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("payroll_payments.id", ondelete="SET NULL"),
            nullable=True,
        ),
        _user_ref("created_by", True),
        _created(),
    )
    op.create_index("ix_worker_advances_user_id", "worker_advances", ["user_id"])
    op.create_index("ix_worker_advances_user", "worker_advances", ["user_id", "given_on"])

    op.add_column(
        "users",
        sa.Column("overtime_rate_per_hour", sa.Numeric(10, 2), nullable=True),
    )
    op.add_column(
        "daily_wages",
        sa.Column(
            "payment_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("payroll_payments.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )

    # Supabase exposes the public schema through its REST API: keep money tables
    # closed to it (this app talks to Postgres directly).
    for table in ("expenses", "payroll_payments", "work_entries", "worker_advances"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.drop_column("daily_wages", "payment_id")
    op.drop_column("users", "overtime_rate_per_hour")
    op.drop_table("worker_advances")
    op.drop_table("work_entries")
    op.drop_table("payroll_payments")
    op.drop_table("expenses")
