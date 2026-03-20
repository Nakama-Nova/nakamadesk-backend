from pydantic import BaseModel, ConfigDict
from uuid import UUID
from datetime import date, datetime
from typing import Optional, List
from decimal import Decimal


class AttendanceBase(BaseModel):
    user_id: UUID
    date: date
    status: str # present, absent, half-day
    daily_wage: Decimal
    payment_status: str = "pending"


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

    model_config = ConfigDict(from_attributes=True)
