import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Numeric, String, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.db.base import Base


class DailyWage(Base):
    """
    SQLAlchemy model for daily wage records.

    Tracks the calculated amount for a specific attendance record,
    along with payment status and transaction references.
    """

    __tablename__ = "daily_wages"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    attendance_id = Column(
        UUID(as_uuid=True), ForeignKey("attendance.id"), unique=True, nullable=False
    )
    amount = Column(Numeric(10, 2), nullable=False)
    # payment_status: pending, paid
    payment_status = Column(String, default="pending")
    transaction_reference = Column(String, nullable=True)
    paid_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    attendance = relationship("Attendance", back_populates="wage_entry")
