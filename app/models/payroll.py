import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Column,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import UUID

from app.db.base import Base


def _id():
    return Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )


def _created():
    return Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=text("now()"),
    )


class PayrollPayment(Base):
    """
    One pay-out to one person: everything owed up to a date (daily wages plus extra
    earnings) minus the advance taken back. gross = gross_daily + gross_extras;
    net_paid = gross - deductions. The labour cost of the business is the GROSS.
    """

    __tablename__ = "payroll_payments"
    __table_args__ = (Index("ix_payroll_payments_user_paid", "user_id", "paid_on"),)

    id = _id()
    user_id = Column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True
    )
    paid_on = Column(Date, nullable=False)
    through = Column(Date, nullable=False)  # work up to and including this date
    gross_daily = Column(Numeric(12, 2), nullable=False, default=0)
    gross_extras = Column(Numeric(12, 2), nullable=False, default=0)
    deductions = Column(Numeric(12, 2), nullable=False, default=0)
    net_paid = Column(Numeric(12, 2), nullable=False)
    method = Column(String, nullable=False, default="cash", server_default="cash")
    reference = Column(String, nullable=True)
    notes = Column(Text, nullable=True)
    paid_by = Column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at = _created()


class WorkEntry(Base):
    """
    Pay on top of (or instead of) the day's wage: a carving paid by the piece, extra
    hours, or a bonus. amount = quantity x rate (a bonus is quantity 1 at the amount).
    """

    __tablename__ = "work_entries"
    __table_args__ = (Index("ix_work_entries_user_status", "user_id", "status"),)

    id = _id()
    user_id = Column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True
    )
    work_date = Column(Date, nullable=False)
    kind = Column(String, nullable=False)  # piece | overtime | bonus
    description = Column(String, nullable=False)
    quantity = Column(Numeric(10, 2), nullable=False, default=1)
    rate = Column(Numeric(10, 2), nullable=False, default=0)
    amount = Column(Numeric(12, 2), nullable=False)
    item_id = Column(
        UUID(as_uuid=True), ForeignKey("items.id", ondelete="SET NULL"), nullable=True
    )
    status = Column(String, nullable=False, default="pending", server_default="pending")
    payment_id = Column(
        UUID(as_uuid=True),
        ForeignKey("payroll_payments.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_by = Column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at = _created()


class WorkerAdvance(Base):
    """
    Ledger of money lent to a worker: positive = given, negative = taken back (from a
    pay-out or paid back in cash). The balance is the sum.
    """

    __tablename__ = "worker_advances"
    __table_args__ = (Index("ix_worker_advances_user", "user_id", "given_on"),)

    id = _id()
    user_id = Column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True
    )
    given_on = Column(Date, nullable=False)
    amount = Column(Numeric(12, 2), nullable=False)
    kind = Column(String, nullable=False)  # advance | recovery
    notes = Column(Text, nullable=True)
    payment_id = Column(
        UUID(as_uuid=True),
        ForeignKey("payroll_payments.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_by = Column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at = _created()
