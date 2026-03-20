from pydantic import BaseModel, ConfigDict, field_validator
from uuid import UUID
from datetime import date, datetime
from typing import Optional
from decimal import Decimal


class AttendanceBase(BaseModel):
    user_id: UUID
    date: date
    status: str  # present, absent, half-day
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
    pass


class AttendanceUpdate(BaseModel):
    status: Optional[str] = None
    daily_wage: Optional[Decimal] = None
    payment_status: Optional[str] = None


class AttendanceResponse(AttendanceBase):
    id: UUID
    recorded_by: UUID
    created_at: datetime
    updated_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)
