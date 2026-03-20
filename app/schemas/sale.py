from datetime import datetime
from typing import List, Optional
from uuid import UUID
from pydantic import BaseModel, ConfigDict, field_validator


class SaleItemCreate(BaseModel):
    item_id: UUID
    quantity: int

    @field_validator("quantity")
    @classmethod
    def validate_quantity(cls, v):
        if v <= 0:
            raise ValueError("quantity must be greater than zero")
        return v


class SaleCreate(BaseModel):
    customer_id: Optional[UUID] = None
    user_id: Optional[UUID] = None
    client_id: Optional[UUID] = None # For idempotency
    items: List[SaleItemCreate]
    order_type: str = "in-store"
    discount: float = 0.0
    payment_method: Optional[str] = None
    payment_status: str = "pending"
    order_status: str = "completed"


class SaleItemResponse(BaseModel):
    id: UUID
    item_id: UUID
    quantity: int
    price_at_sale: float
    gst_percent: float = 0.0
    cgst_amount: float = 0.0
    sgst_amount: float = 0.0
    total_price: float = 0.0
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class SaleResponse(BaseModel):
    id: UUID
    invoice_number: Optional[str] = None
    invoice_date: Optional[datetime] = None
    customer_id: Optional[UUID] = None
    user_id: Optional[UUID] = None
    order_type: str
    sub_total: float
    tax_total: float
    discount: float
    total_amount: float
    payment_status: str
    payment_method: Optional[str] = None
    order_status: str
    created_at: datetime
    updated_at: Optional[datetime] = None
    items: List[SaleItemResponse] = []

    model_config = ConfigDict(from_attributes=True)
