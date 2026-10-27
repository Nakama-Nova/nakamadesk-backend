from datetime import datetime
from decimal import Decimal
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.models.enums import UserRole


class UserBase(BaseModel):
    """
    Base attributes for user accounts and profiles.
    """

    username: str
    email: EmailStr
    full_name: Optional[str] = None
    phone: Optional[str] = None
    role: UserRole = UserRole.WORKER
    status: str = "active"
    is_active: bool = True


class UserCreate(UserBase):
    """
    Schema for creating a new user with a password.
    """

    password: str = Field(min_length=8, max_length=72)
    base_daily_wage: Optional[Decimal] = Field(default=None, ge=0)
    overtime_rate_per_hour: Optional[Decimal] = Field(default=None, ge=0)


class UserUpdate(BaseModel):
    """Owner edits a staff account. Only the fields sent change."""

    full_name: Optional[str] = Field(default=None, max_length=120)
    phone: Optional[str] = Field(default=None, max_length=30)
    email: Optional[EmailStr] = None
    role: Optional[UserRole] = None
    is_active: Optional[bool] = None
    base_daily_wage: Optional[Decimal] = Field(default=None, ge=0)
    overtime_rate_per_hour: Optional[Decimal] = Field(default=None, ge=0)


class PasswordReset(BaseModel):
    new_password: str = Field(min_length=8, max_length=72)


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

    model_config = ConfigDict(from_attributes=True)
