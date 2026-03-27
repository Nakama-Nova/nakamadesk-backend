from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr

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

    password: str


class UserResponse(UserBase):
    """
    Data Transfer Object for user profile information.
    """

    id: UUID
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
