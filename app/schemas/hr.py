"""Request and response shapes for wage history, leave, shifts, holidays and documents."""

from datetime import date, datetime, time
from decimal import Decimal
from typing import Dict, List, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.user import PayType

# ---------------------------------------------------------------- wage history


class WageCreate(BaseModel):
    pay_type: PayType
    amount: Decimal = Field(ge=0, max_digits=12, decimal_places=2)
    effective_from: date
    note: Optional[str] = Field(default=None, max_length=200)


class WageResponse(BaseModel):
    id: UUID
    user_id: UUID
    pay_type: str
    amount: Decimal
    effective_from: date
    note: Optional[str] = None
    created_by: Optional[UUID] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ----------------------------------------------------------------------- leave


class LeaveTypeCreate(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    paid: bool = True
    yearly_days: Optional[Decimal] = Field(default=None, ge=0, max_digits=5)
    active: bool = True


class LeaveTypeUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=60)
    paid: Optional[bool] = None
    yearly_days: Optional[Decimal] = Field(default=None, ge=0, max_digits=5)
    active: Optional[bool] = None


class LeaveTypeResponse(BaseModel):
    id: UUID
    name: str
    paid: bool
    yearly_days: Optional[Decimal] = None
    active: bool

    model_config = ConfigDict(from_attributes=True)


class LeaveRequestCreate(BaseModel):
    leave_type_id: UUID
    from_date: date
    to_date: date
    half_day: bool = False
    reason: Optional[str] = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def _dates(self):
        if self.to_date < self.from_date:
            raise ValueError("The last day cannot be before the first day")
        if self.half_day and self.from_date != self.to_date:
            raise ValueError("A half day is for a single day")
        return self


class LeaveDecision(BaseModel):
    note: Optional[str] = Field(default=None, max_length=300)


class LeaveReject(BaseModel):
    note: str = Field(min_length=1, max_length=300)


class LeaveRequestResponse(BaseModel):
    id: UUID
    user_id: UUID
    user_name: Optional[str] = None
    leave_type_id: UUID
    leave_type_name: Optional[str] = None
    paid: Optional[bool] = None
    from_date: date
    to_date: date
    half_day: bool
    days: Decimal
    reason: Optional[str] = None
    status: str
    decided_by: Optional[UUID] = None
    decided_at: Optional[datetime] = None
    decision_note: Optional[str] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class LeaveBalance(BaseModel):
    leave_type_id: UUID
    name: str
    yearly_days: Optional[Decimal] = None
    used: Decimal
    pending: Decimal
    left: Optional[Decimal] = None  # None = no limit


# ------------------------------------------------------------ shifts, holidays


class ShiftBase(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    start_time: time
    end_time: time
    grace_minutes: int = Field(default=10, ge=0, le=240)
    weekly_off: List[int] = Field(default_factory=list)  # 0 = Monday
    active: bool = True

    @field_validator("weekly_off")
    @classmethod
    def _days(cls, v):
        if any(d < 0 or d > 6 for d in v):
            raise ValueError("Weekdays are 0 (Monday) to 6 (Sunday)")
        return sorted(set(v))


class ShiftCreate(ShiftBase):
    pass


class ShiftResponse(ShiftBase):
    id: UUID

    model_config = ConfigDict(from_attributes=True)


class ShiftAssign(BaseModel):
    shift_id: Optional[UUID] = None


class HolidayCreate(BaseModel):
    date: date
    name: str = Field(min_length=1, max_length=100)
    paid: bool = True


class HolidayResponse(BaseModel):
    id: UUID
    date: date
    name: str
    paid: bool

    model_config = ConfigDict(from_attributes=True)


# ------------------------------------------------------------------- documents

DocKind = Literal["id_proof", "contract", "photo", "other"]
MAX_DOC_BYTES = 10 * 1024 * 1024
DOC_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "application/pdf": ".pdf",
}


class DocumentUploadRequest(BaseModel):
    kind: DocKind
    filename: str = Field(min_length=1, max_length=200)
    content_type: str
    size: int = Field(gt=0)

    @field_validator("content_type")
    @classmethod
    def _type(cls, v):
        v = v.lower().strip()
        if v not in DOC_TYPES:
            raise ValueError("Only photos (JPG, PNG, WebP) and PDF files are accepted")
        return v

    @field_validator("size")
    @classmethod
    def _size(cls, v):
        if v > MAX_DOC_BYTES:
            raise ValueError("Files can be 10 MB at most")
        return v


class DocumentUploadResponse(BaseModel):
    document_id: UUID
    upload_url: str
    headers: Dict[str, str]


class DocumentResponse(BaseModel):
    id: UUID
    user_id: UUID
    kind: str
    filename: str
    content_type: str
    size: int
    uploaded_by: Optional[UUID] = None
    uploaded_at: datetime
    url: Optional[str] = None  # signed, short-lived

    model_config = ConfigDict(from_attributes=True)


# -------------------------------------------------------------------- offboard


class OffboardRequest(BaseModel):
    last_working_date: date
    note: Optional[str] = Field(default=None, max_length=300)


class Settlement(BaseModel):
    user_id: UUID
    last_working_date: date
    deactivates_on: date
    is_active: bool
    pending_daily: Decimal
    pending_extras: Decimal
    advance_balance: Decimal
    gross: Decimal
    net_to_pay: Decimal


class PayslipRequest(BaseModel):
    payment_id: UUID
