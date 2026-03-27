from datetime import datetime
from decimal import Decimal
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, field_validator


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
    discount: Decimal = Decimal("0.00")
    payment_method: Optional[str] = None
    payment_status: str = "pending"
    order_status: str = "completed"


class SaleItemResponse(BaseModel):
    """
    Data Transfer Object for item details within a sale.
    """

    id: UUID
    item_id: UUID
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
