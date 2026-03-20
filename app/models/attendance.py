from sqlalchemy import Column, String, DateTime, Date, ForeignKey, Numeric, text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from datetime import datetime, timezone
import uuid

from app.db.base import Base


class Attendance(Base):
    __tablename__ = "attendance"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    user_id = Column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True
    )
    date = Column(Date, nullable=False, index=True)
    # status: present, absent, half-day
    status = Column(String, nullable=False)
    daily_wage = Column(Numeric(10, 2), nullable=False)
    # payment_status: pending, paid
    payment_status = Column(String, default="pending")
    recorded_by = Column(
        UUID(as_uuid=True), ForeignKey("users.id"), index=True, nullable=False
    )
    client_id = Column(UUID(as_uuid=True), unique=True, nullable=True, index=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    user = relationship("User", foreign_keys=[user_id], backref="attendance_records")
    admin = relationship("User", foreign_keys=[recorded_by])
    wage_entry = relationship(
        "DailyWage",
        back_populates="attendance",
        uselist=False,
        cascade="all, delete-orphan",
    )
