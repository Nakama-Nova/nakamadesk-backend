import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Numeric,
    String,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.db.base import Base


class Attendance(Base):
    """
    SQLAlchemy model for tracking workforce attendance.

    Stores check-in and check-out times, calculates total working hours,
    and links to the corresponding wage entry for the day.
    """

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
    check_in = Column(DateTime, nullable=True)
    check_out = Column(DateTime, nullable=True)
    total_hours = Column(Numeric(10, 2), nullable=True)
    daily_wage = Column(Numeric(10, 2), nullable=False)
    # payment_status: pending, paid
    payment_status = Column(String, default="pending")
    recorded_by = Column(
        UUID(as_uuid=True), ForeignKey("users.id"), index=True, nullable=False
    )
    client_id = Column(UUID(as_uuid=True), unique=True, nullable=True, index=True)
    # Where the phone said it was when a manager marked this row. All five are set by
    # the server from the reported position, never taken from a client as given
    # (see plant_unit_service). location_status: on_site, off_site; empty = no GPS.
    plant_unit_id = Column(
        UUID(as_uuid=True), ForeignKey("plant_units.id"), nullable=True, index=True
    )
    latitude = Column(Float, nullable=True)
    longitude = Column(Float, nullable=True)
    accuracy_m = Column(Float, nullable=True)
    location_status = Column(String, nullable=True)
    # The same five for the check-out. When a person checks themselves in and out, the
    # position is their own phone's; when a manager marks someone, it is the manager's.
    check_out_plant_unit_id = Column(
        UUID(as_uuid=True), ForeignKey("plant_units.id"), nullable=True
    )
    check_out_latitude = Column(Float, nullable=True)
    check_out_longitude = Column(Float, nullable=True)
    check_out_accuracy_m = Column(Float, nullable=True)
    check_out_location_status = Column(String, nullable=True)
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
