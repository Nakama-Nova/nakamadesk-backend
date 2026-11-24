"""HR tables: wage history, leave, shifts, holidays and staff documents."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    Time,
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


class UserWage(Base):
    """One rate in force from a date: the pay type and the amount per day/week/month/hour."""

    __tablename__ = "user_wages"
    __table_args__ = (Index("ix_user_wages_user_from", "user_id", "effective_from"),)

    id = _id()
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    pay_type = Column(String, nullable=False)  # daily, weekly, monthly, hourly
    amount = Column(Numeric(12, 2), nullable=False)
    effective_from = Column(Date, nullable=False)
    note = Column(String, nullable=True)
    created_by = Column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at = _created()


class LeaveType(Base):
    __tablename__ = "leave_types"

    id = _id()
    name = Column(String, nullable=False, unique=True)
    paid = Column(Boolean, nullable=False, default=True, server_default=text("true"))
    yearly_days = Column(Numeric(5, 1), nullable=True)  # None = no limit
    active = Column(Boolean, nullable=False, default=True, server_default=text("true"))


class LeaveRequest(Base):
    __tablename__ = "leave_requests"
    __table_args__ = (Index("ix_leave_requests_user_dates", "user_id", "from_date"),)

    id = _id()
    user_id = Column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True
    )
    leave_type_id = Column(
        UUID(as_uuid=True), ForeignKey("leave_types.id"), nullable=False
    )
    from_date = Column(Date, nullable=False)
    to_date = Column(Date, nullable=False)
    half_day = Column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    days = Column(Numeric(5, 1), nullable=False)
    reason = Column(Text, nullable=True)
    # pending, approved, rejected, cancelled
    status = Column(String, nullable=False, default="pending", server_default="pending")
    decided_by = Column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    decided_at = Column(DateTime(timezone=True), nullable=True)
    decision_note = Column(Text, nullable=True)
    created_at = _created()


class Shift(Base):
    __tablename__ = "shifts"

    id = _id()
    name = Column(String, nullable=False, unique=True)
    start_time = Column(Time, nullable=False)
    end_time = Column(Time, nullable=False)
    grace_minutes = Column(Integer, nullable=False, default=10, server_default="10")
    weekly_off = Column(JSON, nullable=False, default=list)  # 0 = Monday
    active = Column(Boolean, nullable=False, default=True, server_default=text("true"))


class Holiday(Base):
    __tablename__ = "holidays"

    id = _id()
    date = Column(Date, nullable=False, unique=True, index=True)
    name = Column(String, nullable=False)
    paid = Column(Boolean, nullable=False, default=True, server_default=text("true"))
    created_at = _created()


class UserDocument(Base):
    """An ID proof scan, contract or photo. The bytes live in the private bucket."""

    __tablename__ = "user_documents"

    id = _id()
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    kind = Column(String, nullable=False)  # id_proof, contract, photo, other
    filename = Column(String, nullable=False)
    content_type = Column(String, nullable=False)
    size = Column(Integer, nullable=False)
    storage_key = Column(String, nullable=False)
    confirmed = Column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    uploaded_by = Column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    uploaded_at = _created()
