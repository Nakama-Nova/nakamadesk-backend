from pydantic import BaseModel, ConfigDict, Field
from uuid import UUID
from datetime import datetime
from typing import Optional
from decimal import Decimal


class RawMaterialBase(BaseModel):
    """
    Base attributes for raw material records.
    """

    name: str
    unit: str = "pcs"
    current_price: Decimal = Field(default=0.0, decimal_places=2)
    stock: Decimal = Field(default=0.0, decimal_places=2)


class RawMaterialCreate(RawMaterialBase):
    """
    Schema for adding a new raw material to the system.
    """


class RawMaterialUpdate(BaseModel):
    """
    Schema for updating raw material stock levels or pricing.
    """

    name: Optional[str] = None
    unit: Optional[str] = None
    current_price: Optional[Decimal] = None
    stock: Optional[Decimal] = None


class RawMaterialResponse(RawMaterialBase):
    """
    Data Transfer Object for raw material details in API responses.
    """

    id: UUID
    created_at: datetime
    updated_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class RawMaterialPriceHistoryResponse(BaseModel):
    """
    Schema for historical price records of a raw material.
    """

    id: UUID
    material_id: UUID
    price: Decimal
    source: str
    recorded_at: datetime

    model_config = ConfigDict(from_attributes=True)
