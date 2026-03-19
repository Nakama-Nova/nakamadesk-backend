from typing import Optional
from uuid import UUID
from datetime import datetime
from pydantic import BaseModel, ConfigDict, field_validator

from app.core.constants import GST_ALLOWED


class ItemBase(BaseModel):
    sku: str
    name: str
    description: Optional[str] = None
    category_id: Optional[UUID] = None
    brand_id: Optional[UUID] = None
    unit: str = "pcs"
    purchase_price: float = 0.0
    selling_price: float = 0.0
    gst_percent: float = 0.0
    hsn_code: Optional[str] = None
    current_stock: int = 0
    min_stock: int = 5
    image_url: Optional[str] = None
    is_active: bool = True


class ItemCreate(ItemBase):
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
    sku: Optional[str] = None
    name: Optional[str] = None
    description: Optional[str] = None
    category_id: Optional[UUID] = None
    brand_id: Optional[UUID] = None
    unit: Optional[str] = None
    purchase_price: Optional[float] = None
    selling_price: Optional[float] = None
    gst_percent: Optional[float] = None
    hsn_code: Optional[str] = None
    current_stock: Optional[int] = None
    min_stock: Optional[int] = None
    image_url: Optional[str] = None
    is_active: Optional[bool] = None


class ItemResponse(ItemBase):
    id: UUID
    version_id: int
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
