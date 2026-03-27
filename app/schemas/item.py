from datetime import datetime
from decimal import Decimal
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, field_validator

from app.core.constants import GST_ALLOWED


class ItemBase(BaseModel):
    """
    Base attributes for inventory item records.
    """

    sku: str
    name: str
    description: Optional[str] = None
    category_id: Optional[UUID] = None
    brand_id: Optional[UUID] = None
    unit: str = "pcs"
    purchase_price: Decimal = Decimal("0.00")
    selling_price: Decimal = Decimal("0.00")
    gst_percent: Decimal = Decimal("0.00")
    hsn_code: Optional[str] = None
    current_stock: int = 0
    min_stock: int = 5
    image_url: Optional[str] = None
    production_cost: Decimal = Decimal("0.00")
    is_active: bool = True


class ItemCreate(ItemBase):
    """
    Schema for adding a new item to the inventory.
    """

    @field_validator("gst_percent")
    @classmethod
    def validate_gst(cls, v):
        if v not in GST_ALLOWED:
            raise ValueError(f"gst_percent must be one of {GST_ALLOWED}")
        return v

    @field_validator("purchase_price", "selling_price")
    @classmethod
    def validate_price(cls, v):
        if v < 0:
            raise ValueError("price must not be negative")
        return v


class ItemUpdate(BaseModel):
    """
    Schema for updating individual fields of an inventory item.
    """

    sku: Optional[str] = None
    name: Optional[str] = None
    description: Optional[str] = None
    category_id: Optional[UUID] = None
    brand_id: Optional[UUID] = None
    unit: Optional[str] = None
    purchase_price: Optional[Decimal] = None
    selling_price: Optional[Decimal] = None
    gst_percent: Optional[Decimal] = None
    hsn_code: Optional[str] = None
    current_stock: Optional[int] = None
    min_stock: Optional[int] = None
    image_url: Optional[str] = None
    production_cost: Optional[Decimal] = None
    is_active: Optional[bool] = None


class ItemResponse(ItemBase):
    """
    Data Transfer Object for inventory items in API responses.
    """

    id: UUID
    version_id: int
    created_at: datetime
    updated_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)
