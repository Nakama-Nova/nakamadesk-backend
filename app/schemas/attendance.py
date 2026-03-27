from datetime import date, datetime
from decimal import Decimal
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, field_validator


class AttendanceBase(BaseModel):
    """
    Base attributes for workforce attendance records.
    """

    user_id: UUID
    date: date
    status: str  # present, absent, half-day
    check_in: Optional[datetime] = None
    check_out: Optional[datetime] = None
    total_hours: Optional[Decimal] = None
    daily_wage: Decimal
    payment_status: str = "pending"
    client_id: Optional[UUID] = None  # For idempotency

    @field_validator("status")
    @classmethod
    def validate_status(cls, v):
        if v not in ["present", "absent", "half-day"]:
            raise ValueError("status must be present, absent, or half-day")
        return v

    @field_validator("daily_wage")
    @classmethod
    def validate_wage(cls, v):
        if v < 0:
            raise ValueError("daily_wage cannot be negative")
        return v


class AttendanceCreate(AttendanceBase):
    """
    Schema for recording new attendance (Check-in or Manual entry).
    """


class AttendanceUpdate(BaseModel):
    """
    Schema for modifying existing attendance records (Check-out or Correction).
    """

    status: Optional[str] = None
    check_in: Optional[datetime] = None
    check_out: Optional[datetime] = None
    total_hours: Optional[Decimal] = None
    daily_wage: Optional[Decimal] = None
    payment_status: Optional[str] = None


class AttendanceResponse(AttendanceBase):
    """
    Data Transfer Object for attendance history and details.
    """

    id: UUID
    recorded_by: UUID
    created_at: datetime
    updated_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)
