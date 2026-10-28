import re
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator

from app.core.constants import GST_ALLOWED
from app.services import pricing

SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


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
    profit_percent: Optional[Decimal] = Field(
        default=None, ge=0, le=1000, max_digits=7, decimal_places=2
    )
    hsn_code: Optional[str] = None
    current_stock: int = 0
    min_stock: int = 5
    image_url: Optional[str] = None
    production_cost: Decimal = Decimal("0.00")
    is_active: bool = True

    # Public catalogue (website)
    slug: Optional[str] = None
    is_published: bool = False
    made_to_order: bool = False
    lead_time_days: Optional[int] = None
    short_desc: Optional[str] = None
    wood_type: Optional[str] = None
    dimensions_mm: Optional[Dict[str, Any]] = None
    is_furniture: bool = False
    size_presets: Optional[Dict[str, Any]] = None
    item_type: Literal["furniture", "sculpture"] = "furniture"

    @field_validator("slug")
    @classmethod
    def validate_slug(cls, v):
        if v is not None and not SLUG_RE.match(v):
            raise ValueError("slug must be lowercase letters, digits and hyphens")
        return v


class ItemCreate(ItemBase):
    """
    Schema for adding a new item to the inventory. Leave sku out and the server gives
    the next code for the item's type (FUR-0001, SCU-0001...).
    """

    sku: Optional[str] = None

    @field_validator("sku")
    @classmethod
    def blank_sku_means_auto(cls, v):
        v = (v or "").strip()
        return v or None

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
    profit_percent: Optional[Decimal] = Field(
        default=None, ge=0, le=1000, max_digits=7, decimal_places=2
    )
    hsn_code: Optional[str] = None
    current_stock: Optional[int] = None
    min_stock: Optional[int] = None
    image_url: Optional[str] = None
    production_cost: Optional[Decimal] = None
    is_active: Optional[bool] = None
    slug: Optional[str] = None
    is_published: Optional[bool] = None
    made_to_order: Optional[bool] = None
    lead_time_days: Optional[int] = None
    short_desc: Optional[str] = None
    wood_type: Optional[str] = None
    dimensions_mm: Optional[Dict[str, Any]] = None
    is_furniture: Optional[bool] = None
    size_presets: Optional[Dict[str, Any]] = None
    item_type: Optional[Literal["furniture", "sculpture"]] = None

    @field_validator("slug")
    @classmethod
    def validate_slug(cls, v):
        if v is not None and not SLUG_RE.match(v):
            raise ValueError("slug must be lowercase letters, digits and hyphens")
        return v


class ItemResponse(ItemBase):
    """
    Data Transfer Object for inventory items in API responses.
    """

    id: UUID
    version_id: int
    created_at: datetime
    updated_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)

    @computed_field
    @property
    def price_with_gst(self) -> Decimal:
        """What a customer pays: the selling price plus its GST."""
        return pricing.with_gst(self.selling_price, self.gst_percent)
