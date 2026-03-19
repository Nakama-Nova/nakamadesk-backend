from datetime import datetime, timezone
from sqlalchemy import Column, DateTime, Float, ForeignKey, String, text
from sqlalchemy.dialects.postgresql import UUID
import uuid

from app.db.base import Base


class DailyWage(Base):
    __tablename__ = "daily_wages"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("gen_random_uuid()"))
    attendance_id = Column(UUID(as_uuid=True), ForeignKey("attendance.id"), unique=True)
    amount = Column(Float, default=0.0)
    payment_status = Column(String, default="pending") # pending, paid
    transaction_ref = Column(String, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
