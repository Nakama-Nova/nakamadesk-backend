from sqlalchemy import Column, String, DateTime, ForeignKey, Numeric, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from datetime import datetime, timezone
import uuid

from app.db.base import Base


class DailyWage(Base):
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
    total_amount = Column(Numeric(10, 2), nullable=False)
    # payment_status: pending, paid
    payment_status = Column(String, default="pending")
    transaction_ref = Column(String, nullable=True)
    paid_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    attendance = relationship("Attendance", back_populates="wage_entry")
