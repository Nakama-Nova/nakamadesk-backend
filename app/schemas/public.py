"""Schemas for the public (no-login) website API. Never include cost fields."""

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class PublicImage(BaseModel):
    url: str
    alt: Optional[str] = None


class PublicProduct(BaseModel):
    slug: str
    name: str
    short_desc: Optional[str] = None
    price: Decimal
    made_to_order: bool
    lead_time_days: Optional[int] = None
    in_stock: bool
    wood_type: Optional[str] = None
    dimensions_mm: Optional[Dict[str, Any]] = None
    category: Optional[str] = None
    images: List[PublicImage] = []
    model_url: Optional[str] = None  # .glb for the 3D viewer, when there is one


class CheckoutLine(BaseModel):
    slug: str
    quantity: int = Field(default=1, ge=1, le=20)


class CheckoutRequest(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    phone: str
    email: Optional[EmailStr] = None
    address: str = Field(min_length=5, max_length=300)
    town: str = Field(min_length=2, max_length=100)
    pincode: str = Field(pattern=r"^\d{6}$")
    note: Optional[str] = Field(default=None, max_length=500)
    # advance_upi: we confirm by phone, then the customer pays an advance by UPI.
    pay_option: Literal["advance_upi", "on_delivery"] = "advance_upi"
    items: List[CheckoutLine] = Field(min_length=1, max_length=20)


class EnquiryRequest(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    phone: str
    what: str = Field(min_length=2, max_length=200)
    description: str = Field(min_length=5, max_length=2000)
    wood: Optional[str] = Field(default=None, max_length=100)
    size: Optional[str] = Field(default=None, max_length=100)
    budget: Optional[str] = Field(default=None, max_length=100)


class PublicOrderLine(BaseModel):
    name: str
    quantity: int
    unit_price: Decimal


class PublicOrder(BaseModel):
    order_number: str
    status: str
    order_type: str
    placed_on: datetime
    expected_delivery: Optional[date] = None
    total: Decimal
    advance_paid: Decimal
    balance_due: Decimal
    items: List[PublicOrderLine]


class DeliveryCheck(BaseModel):
    pincode: str
    serviceable: bool


class AssetResponse(BaseModel):
    id: UUID
    item_id: UUID
    kind: str
    url: str
    alt: Optional[str] = None
    sort: int
    bytes: int

    model_config = ConfigDict(from_attributes=True)
