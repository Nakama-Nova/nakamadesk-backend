import re
from datetime import date, datetime
from decimal import Decimal
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

PayType = Literal["daily", "weekly", "monthly", "hourly"]
Employment = Literal["full_time", "part_time"]
IdProofType = Literal["aadhaar", "pan", "voter", "driving", "other"]
UPI_RE = re.compile(r"^[A-Za-z0-9._\-]{2,64}@[A-Za-z0-9]{2,32}$")


class ProfileFields(BaseModel):
    """What a person's staff record holds beyond the account (all optional)."""

    employment: Optional[Employment] = None
    joining_date: Optional[date] = None
    last_working_date: Optional[date] = None
    address: Optional[str] = Field(default=None, max_length=300)
    emergency_contact_name: Optional[str] = Field(default=None, max_length=120)
    emergency_contact_phone: Optional[str] = Field(default=None, max_length=30)
    pay_upi: Optional[str] = Field(default=None, max_length=80)
    id_proof_type: Optional[IdProofType] = None
    # only the last four characters of the number are ever kept, never the whole thing
    id_proof_last4: Optional[str] = Field(default=None, max_length=4)

    @field_validator("pay_upi")
    @classmethod
    def _upi(cls, v):
        if v is None:
            return None
        v = v.strip()
        if not v:
            return None
        if not UPI_RE.match(v):
            raise ValueError("That is not a UPI id (like name@bank)")
        return v

    @field_validator("id_proof_last4")
    @classmethod
    def _last4(cls, v):
        if v is None:
            return None
        v = v.strip()
        if not v:
            return None
        if not re.fullmatch(r"[A-Za-z0-9]{1,4}", v):
            raise ValueError("Give only the last four letters or digits of the number")
        return v


class UserBase(BaseModel):
    """
    Base attributes for user accounts and profiles.
    """

    username: str
    email: EmailStr
    full_name: Optional[str] = None
    phone: Optional[str] = None
    role: str = Field(default="worker", max_length=60)  # a key from /iam/roles
    status: str = "active"
    is_active: bool = True


class UserCreate(ProfileFields, UserBase):
    """
    Schema for creating a new user. Leave `password` out and the person is emailed a code
    to choose their own (the owner never types or sees one); the command line, which has
    nobody to email, still passes one.
    """

    password: Optional[str] = Field(default=None, min_length=8, max_length=72)
    base_daily_wage: Optional[Decimal] = Field(default=None, ge=0)
    pay_type: Optional[PayType] = None
    pay_amount: Optional[Decimal] = Field(default=None, ge=0, max_digits=12)
    overtime_rate_per_hour: Optional[Decimal] = Field(default=None, ge=0)
    reports_to_id: Optional[UUID] = None  # their manager


class UserUpdate(ProfileFields):
    """Owner edits a staff account. Only the fields sent change."""

    full_name: Optional[str] = Field(default=None, max_length=120)
    phone: Optional[str] = Field(default=None, max_length=30)
    email: Optional[EmailStr] = None
    role: Optional[str] = Field(default=None, max_length=60)
    is_active: Optional[bool] = None
    base_daily_wage: Optional[Decimal] = Field(default=None, ge=0)
    pay_type: Optional[PayType] = None
    pay_amount: Optional[Decimal] = Field(default=None, ge=0, max_digits=12)
    overtime_rate_per_hour: Optional[Decimal] = Field(default=None, ge=0)
    # Send a manager's id to set it, null to clear it, leave it out to keep it.
    reports_to_id: Optional[UUID] = None


class PasswordReset(BaseModel):
    new_password: str = Field(min_length=8, max_length=72)


class ForgotPassword(BaseModel):
    email: EmailStr


class SetPasswordWithCode(PasswordReset):
    email: EmailStr
    code: str = Field(min_length=4, max_length=16)


class PasswordChange(PasswordReset):
    current_password: str


class UserResponse(UserBase):
    """
    Data Transfer Object for user profile information.
    """

    id: UUID
    created_at: datetime
    base_daily_wage: Optional[Decimal] = None
    overtime_rate_per_hour: Optional[Decimal] = None
    reports_to_id: Optional[UUID] = None
    pay_type: Optional[str] = None
    pay_amount: Optional[Decimal] = None
    employment: Optional[str] = None
    shift_id: Optional[UUID] = None
    staff_code: Optional[str] = None
    joining_date: Optional[date] = None
    last_working_date: Optional[date] = None
    address: Optional[str] = None
    emergency_contact_name: Optional[str] = None
    emergency_contact_phone: Optional[str] = None
    pay_upi: Optional[str] = None
    id_proof_type: Optional[str] = None
    id_proof_last4: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)
