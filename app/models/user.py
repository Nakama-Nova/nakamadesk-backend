import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import UUID

from app.db.base import Base
from app.models.enums import UserRole


class User(Base):
    """
    SQLAlchemy model for system users and workforce.

    Manages authentication credentials, Role-Based Access Control (RBAC) levels,
    and profile data for both administrative staff and field workers.
    """

    __tablename__ = "users"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    username = Column(String, unique=True, index=True, nullable=False)
    email = Column(String, unique=True, index=True, nullable=False)
    password_hash = Column(String, nullable=False)
    full_name = Column(String, nullable=True)
    phone = Column(String, nullable=True)
    role = Column(
        String, nullable=False, default=UserRole.WORKER
    )  # owner, manager, sales, achari, worker, artisan, accountant
    status = Column(String, default="active")  # active, inactive, suspended
    is_active = Column(Boolean, default=True)
    base_daily_wage = Column(Numeric(10, 2), default=0.0)
    overtime_rate_per_hour = Column(Numeric(10, 2), nullable=True)
    # Bumped on password change/reset, role change and switch-off; a token carries the
    # number it was issued under, so bumping it ends every session of this user at once.
    token_version = Column(Integer, nullable=False, default=0, server_default=text("0"))
    # Who this person reports to (set by the owner; see team_service). Empty = top of the
    # chart. Switching the boss off or deleting them leaves their people unassigned.
    reports_to_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # HR (see wage_service): daily, weekly, monthly or hourly; pay_amount is the rate for
    # that type; base_daily_wage stays the daily rate in force so older screens work.
    pay_type = Column(String, nullable=False, default="daily", server_default="daily")
    employment = Column(
        String, nullable=False, default="full_time", server_default="full_time"
    )
    pay_amount = Column(Numeric(12, 2), nullable=True)
    shift_id = Column(
        UUID(as_uuid=True),
        ForeignKey("shifts.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    joining_date = Column(Date, nullable=True)
    last_working_date = Column(Date, nullable=True)
    address = Column(String, nullable=True)
    emergency_contact_name = Column(String, nullable=True)
    emergency_contact_phone = Column(String, nullable=True)
    pay_upi = Column(String, nullable=True)
    id_proof_type = Column(String, nullable=True)
    id_proof_last4 = Column(String(4), nullable=True)  # never the full number
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
