from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class CodeRequest(BaseModel):
    email: EmailStr


class CodeSent(BaseModel):
    message: str = "If that address can receive email, a code is on its way."


class VerifyRequest(BaseModel):
    email: EmailStr
    code: str = Field(pattern=r"^\d{6}$")
    # Needed only the first time (when the code creates the account).
    name: Optional[str] = Field(default=None, max_length=100)
    accept_terms: bool = False


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int  # seconds
    is_new_account: bool


class AccountOut(BaseModel):
    id: UUID
    email: str
    name: Optional[str] = None
    phone: Optional[str] = None
    marketing_consent: bool = False
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class AccountUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    phone: Optional[str] = Field(default=None, pattern=r"^\+?\d[\d ]{6,14}$")
    marketing_consent: Optional[bool] = None
