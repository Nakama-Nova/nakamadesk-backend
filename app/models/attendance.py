from datetime import datetime
from sqlalchemy import Column, Date, DateTime, Float, ForeignKey, String, text
from sqlalchemy.dialects.postgresql import UUID
import uuid

from app.db.base import Base


class Attendance(Base):
    __tablename__ = "attendance"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("gen_random_uuid()"))
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), index=True)
    date = Column(Date, default=datetime.now().date)
    check_in = Column(DateTime, nullable=True)
    check_out = Column(DateTime, nullable=True)
    status = Column(String, default="present") # present, absent, half-day
    wage_rate_override = Column(Float, nullable=True)
    recorded_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
