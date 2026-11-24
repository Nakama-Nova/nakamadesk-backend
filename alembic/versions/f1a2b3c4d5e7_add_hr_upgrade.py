"""HR upgrade: pay types and wage history, leave, shifts, holidays, profile, documents

Revision ID: f1a2b3c4d5e7
Revises: d1e2f3a4b5c6
Create Date: 2026-11-24 18:00:00.000000

Adds the columns on users (pay type, rate, shift, joining/last day, address, emergency
contact, UPI, last four of an ID number), the tables user_wages, leave_types,
leave_requests, shifts, holidays and user_documents, and the leave / shift columns on
attendance. Gives every existing person a first wage-history row (what they are paid
today, from the day they were added) and seeds Casual, Sick and Unpaid leave. Nothing
existing is rewritten: base_daily_wage stays as it is.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "f1a2b3c4d5e7"
down_revision: Union[str, Sequence[str], None] = "d1e2f3a4b5c6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

UUID = postgresql.UUID(as_uuid=True)


def _id():
    return sa.Column(
        "id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")
    )


def _created(name="created_at"):
    return sa.Column(
        name,
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("now()"),
    )


def upgrade() -> None:
    op.create_table(
        "shifts",
        _id(),
        sa.Column("name", sa.String(), nullable=False, unique=True),
        sa.Column("start_time", sa.Time(), nullable=False),
        sa.Column("end_time", sa.Time(), nullable=False),
        sa.Column("grace_minutes", sa.Integer(), nullable=False, server_default="10"),
        sa.Column("weekly_off", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column(
            "active", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
    )
    op.create_table(
        "holidays",
        _id(),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("paid", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        _created(),
    )
    op.create_index("ix_holidays_date", "holidays", ["date"], unique=True)
    op.create_table(
        "leave_types",
        _id(),
        sa.Column("name", sa.String(), nullable=False, unique=True),
        sa.Column("paid", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("yearly_days", sa.Numeric(5, 1), nullable=True),
        sa.Column(
            "active", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
    )
    op.create_table(
        "leave_requests",
        _id(),
        sa.Column("user_id", UUID, sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "leave_type_id", UUID, sa.ForeignKey("leave_types.id"), nullable=False
        ),
        sa.Column("from_date", sa.Date(), nullable=False),
        sa.Column("to_date", sa.Date(), nullable=False),
        sa.Column(
            "half_day", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column("days", sa.Numeric(5, 1), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column(
            "decided_by",
            UUID,
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_note", sa.Text(), nullable=True),
        _created(),
    )
    op.create_index("ix_leave_requests_user_id", "leave_requests", ["user_id"])
    op.create_index(
        "ix_leave_requests_user_dates", "leave_requests", ["user_id", "from_date"]
    )
    op.create_table(
        "user_wages",
        _id(),
        sa.Column(
            "user_id",
            UUID,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("pay_type", sa.String(), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("note", sa.String(), nullable=True),
        sa.Column(
            "created_by",
            UUID,
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        _created(),
    )
    op.create_index("ix_user_wages_user_id", "user_wages", ["user_id"])
    op.create_index(
        "ix_user_wages_user_from", "user_wages", ["user_id", "effective_from"]
    )
    op.create_table(
        "user_documents",
        _id(),
        sa.Column(
            "user_id",
            UUID,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("filename", sa.String(), nullable=False),
        sa.Column("content_type", sa.String(), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("storage_key", sa.String(), nullable=False),
        sa.Column(
            "confirmed", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column(
            "uploaded_by",
            UUID,
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        _created("uploaded_at"),
    )
    op.create_index("ix_user_documents_user_id", "user_documents", ["user_id"])

    # users
    op.add_column(
        "users",
        sa.Column("pay_type", sa.String(), nullable=False, server_default="daily"),
    )
    op.add_column(
        "users",
        sa.Column(
            "employment", sa.String(), nullable=False, server_default="full_time"
        ),
    )
    op.add_column("users", sa.Column("pay_amount", sa.Numeric(12, 2), nullable=True))
    op.add_column(
        "users",
        sa.Column(
            "shift_id",
            UUID,
            sa.ForeignKey("shifts.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("ix_users_shift_id", "users", ["shift_id"])
    op.add_column("users", sa.Column("joining_date", sa.Date(), nullable=True))
    op.add_column("users", sa.Column("last_working_date", sa.Date(), nullable=True))
    op.add_column("users", sa.Column("address", sa.String(), nullable=True))
    op.add_column(
        "users", sa.Column("emergency_contact_name", sa.String(), nullable=True)
    )
    op.add_column(
        "users", sa.Column("emergency_contact_phone", sa.String(), nullable=True)
    )
    op.add_column("users", sa.Column("pay_upi", sa.String(), nullable=True))
    op.add_column("users", sa.Column("id_proof_type", sa.String(), nullable=True))
    op.add_column("users", sa.Column("id_proof_last4", sa.String(4), nullable=True))

    # attendance
    op.add_column(
        "attendance",
        sa.Column(
            "leave_type_id", UUID, sa.ForeignKey("leave_types.id"), nullable=True
        ),
    )
    op.add_column(
        "attendance",
        sa.Column(
            "leave_request_id", UUID, sa.ForeignKey("leave_requests.id"), nullable=True
        ),
    )
    op.create_index(
        "ix_attendance_leave_request_id", "attendance", ["leave_request_id"]
    )
    op.add_column(
        "attendance", sa.Column("leave_half_day", sa.Boolean(), nullable=True)
    )
    op.add_column("attendance", sa.Column("paid", sa.Boolean(), nullable=True))
    op.add_column(
        "attendance",
        sa.Column("late_minutes", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "attendance",
        sa.Column(
            "early_leave_minutes", sa.Integer(), nullable=False, server_default="0"
        ),
    )

    # Everyone already working here: their pay today becomes their first history row.
    op.execute("UPDATE users SET pay_amount = base_daily_wage WHERE pay_amount IS NULL")
    op.execute("""
        INSERT INTO user_wages (user_id, pay_type, amount, effective_from, note)
        SELECT id, 'daily', COALESCE(base_daily_wage, 0),
               COALESCE(created_at::date, CURRENT_DATE), 'Starting rate'
        FROM users
        """)
    op.execute("""
        INSERT INTO leave_types (name, paid, yearly_days) VALUES
          ('Casual leave', true, 12),
          ('Sick leave', true, 6),
          ('Unpaid leave', false, NULL)
        """)


def downgrade() -> None:
    for col in ("early_leave_minutes", "late_minutes", "paid", "leave_half_day"):
        op.drop_column("attendance", col)
    op.drop_index("ix_attendance_leave_request_id", table_name="attendance")
    op.drop_column("attendance", "leave_request_id")
    op.drop_column("attendance", "leave_type_id")
    op.drop_index("ix_users_shift_id", table_name="users")
    for col in (
        "id_proof_last4",
        "id_proof_type",
        "pay_upi",
        "emergency_contact_phone",
        "emergency_contact_name",
        "address",
        "last_working_date",
        "joining_date",
        "shift_id",
        "pay_amount",
        "employment",
        "pay_type",
    ):
        op.drop_column("users", col)
    op.drop_table("user_documents")
    op.drop_table("user_wages")
    op.drop_table("leave_requests")
    op.drop_table("leave_types")
    op.drop_table("holidays")
    op.drop_table("shifts")
