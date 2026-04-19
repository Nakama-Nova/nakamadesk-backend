from datetime import datetime
from decimal import Decimal
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class PurchaseItemBase(BaseModel):
    """Base schema for purchase items."""

    item_id: Optional[UUID] = None
    raw_material_id: Optional[UUID] = None
    quantity: Decimal = Field(..., gt=0)
    unit_price: Decimal = Field(..., ge=0)
    gst_percent: Decimal = Field(default=0.0, ge=0)


class PurchaseItemCreate(PurchaseItemBase):
    """Schema for creating a purchase item."""


class PurchaseItemResponse(PurchaseItemBase):
    """Schema for purchase item response."""

    id: UUID
    purchase_id: UUID
    line_total: Decimal

    model_config = ConfigDict(from_attributes=True)


class PurchaseBase(BaseModel):
    """Base schema for purchases."""

    supplier_id: Optional[UUID] = None
    invoice_number: Optional[str] = None
    purchase_date: datetime = Field(default_factory=datetime.utcnow)
    purchase_type: str = "tax_invoice"  # tax_invoice | purchase_voucher
    is_itc_eligible: bool = True
    notes: Optional[str] = None
    payment_status: str = "pending"


class PurchaseCreate(PurchaseBase):
    """Schema for creating a new purchase."""

    items: List[PurchaseItemCreate]


class PurchaseResponse(PurchaseBase):
    """Schema for purchase response."""

    id: UUID
    total_amount: Decimal
    tax_total: Decimal
    status: str
    created_at: datetime
    items: List[PurchaseItemResponse] = []

    model_config = ConfigDict(from_attributes=True)
