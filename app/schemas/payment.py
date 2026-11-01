from datetime import datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, Field


class PayRequest(BaseModel):
    # Leave out to pay the whole balance; a smaller amount is an advance.
    amount: Optional[Decimal] = Field(
        default=None, gt=0, max_digits=12, decimal_places=2
    )


class GuestPayRequest(PayRequest):
    phone: str = Field(max_length=20)


class PaymentConfirm(BaseModel):
    """What Razorpay Checkout hands the browser when a payment succeeds."""

    razorpay_order_id: str = Field(max_length=100)
    razorpay_payment_id: str = Field(max_length=100)
    razorpay_signature: str = Field(max_length=200)


class GuestPaymentConfirm(PaymentConfirm):
    phone: str = Field(max_length=20)


class PaymentStart(BaseModel):
    """Everything the storefront needs to open Razorpay Checkout."""

    order_number: str
    key_id: str  # public; the secret never leaves the server
    mode: str  # "test" or "live"
    razorpay_order_id: str
    amount_paise: int
    currency: str = "INR"
    hold_expires_at: Optional[datetime] = None  # when the reserved stock is let go
    name: Optional[str] = None
    email: Optional[str] = None
    contact: Optional[str] = None
