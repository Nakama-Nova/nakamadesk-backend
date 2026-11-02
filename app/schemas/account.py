from datetime import datetime
from typing import List, Literal, Optional
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    model_validator,
)

from app.schemas.public import CheckoutLine


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
    phone_verified_at: Optional[datetime] = None  # set once the SMS code was typed
    marketing_consent: bool = False
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class PhoneCodeRequest(BaseModel):
    phone: str = Field(min_length=7, max_length=20)


class PhoneCodeSent(BaseModel):
    message: str = "A code is on its way by SMS."


class PhoneVerifyRequest(BaseModel):
    phone: str = Field(min_length=7, max_length=20)
    code: str = Field(pattern=r"^\d{6}$")


class AccountUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    phone: Optional[str] = Field(default=None, pattern=r"^\+?\d[\d ]{6,14}$")
    marketing_consent: Optional[bool] = None


class AddressIn(BaseModel):
    label: Optional[str] = Field(default=None, max_length=30)
    recipient_name: str = Field(min_length=2, max_length=100)
    phone: Optional[str] = Field(default=None, max_length=20)
    address: str = Field(min_length=5, max_length=300)
    town: str = Field(min_length=2, max_length=100)
    pincode: str = Field(pattern=r"^\d{6}$")
    is_default: bool = False


class AddressOut(AddressIn):
    id: UUID
    model_config = ConfigDict(from_attributes=True)


class AccountCheckoutRequest(BaseModel):
    """
    Place an order as a signed-in shopper. Say where to deliver with a saved
    `address_id` or a one-off `address` (not both). Name and phone default to the
    address, then the account; prices always come from the server.
    """

    items: List[CheckoutLine] = Field(min_length=1, max_length=20)
    pay_option: Literal["advance_upi", "on_delivery"] = "advance_upi"
    note: Optional[str] = Field(default=None, max_length=500)
    address_id: Optional[UUID] = None
    address: Optional[AddressIn] = None
    name: Optional[str] = Field(default=None, max_length=100)
    phone: Optional[str] = Field(default=None, max_length=20)

    @model_validator(mode="after")
    def one_address(self):
        if (self.address_id is None) == (self.address is None):
            raise ValueError("give either address_id or address")
        return self


class AccountEnquiryRequest(BaseModel):
    what: str = Field(min_length=2, max_length=200)
    description: str = Field(min_length=5, max_length=2000)
    wood: Optional[str] = Field(default=None, max_length=100)
    size: Optional[str] = Field(default=None, max_length=100)
    budget: Optional[str] = Field(default=None, max_length=100)
    name: Optional[str] = Field(default=None, max_length=100)
    phone: Optional[str] = Field(default=None, max_length=20)
