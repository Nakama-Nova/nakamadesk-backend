from datetime import datetime
from decimal import Decimal
from typing import List, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class SaleItemCreate(BaseModel):
    """
    Schema for adding a product to a new sale transaction.
    """

    item_id: UUID
    quantity: int

    @field_validator("quantity")
    @classmethod
    def validate_quantity(cls, v):
        if v <= 0:
            raise ValueError("quantity must be greater than zero")
        return v


class SaleCreate(BaseModel):
    """
    Schema for creating a new sales transaction.
    """

    customer_id: Optional[UUID] = None
    user_id: Optional[UUID] = None
    client_id: Optional[UUID] = None  # For idempotency
    items: List[SaleItemCreate]
    order_type: str = "in-store"
    # Where it was sold. Left out, the Shop is used when there is exactly one.
    location_id: Optional[UUID] = None
    discount: Decimal = Field(default=Decimal("0.00"), ge=0)
    payment_method: Optional[str] = None
    payment_status: str = "pending"
    order_status: str = "completed"


class SaleItemResponse(BaseModel):
    """
    Data Transfer Object for item details within a sale.
    """

    id: UUID
    item_id: Optional[UUID] = None  # none for a custom piece or other charges
    item_name: Optional[str] = None
    quantity: int
    price_at_sale: Decimal
    gst_percent: Decimal = Decimal("0.00")
    cgst_amount: Decimal = Decimal("0.00")
    sgst_amount: Decimal = Decimal("0.00")
    total_price: Decimal = Decimal("0.00")
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class SaleResponse(BaseModel):
    """
    Data Transfer Object for a complete sales transaction.
    """

    id: UUID
    invoice_number: Optional[str] = None
    invoice_date: Optional[datetime] = None
    customer_id: Optional[UUID] = None
    user_id: Optional[UUID] = None
    location_id: Optional[UUID] = None
    order_type: str
    sub_total: Decimal
    tax_total: Decimal
    discount: Decimal
    total_amount: Decimal
    payment_status: str
    payment_method: Optional[str] = None
    order_status: str
    created_at: datetime
    updated_at: Optional[datetime] = None
    items: List[SaleItemResponse] = []

    model_config = ConfigDict(from_attributes=True)


class SaleUpdate(BaseModel):
    """Corrections to a bill's customer and payment details. Only the fields sent change."""

    customer_id: Optional[UUID] = None
    payment_status: Optional[Literal["pending", "partial", "paid"]] = None
    payment_method: Optional[str] = Field(default=None, max_length=30)


class SaleCancel(BaseModel):
    """Why a bill is being cancelled (it stays in the bill history)."""

    reason: str = Field(min_length=3, max_length=300)
