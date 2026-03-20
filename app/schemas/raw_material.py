from pydantic import BaseModel, ConfigDict, Field
from uuid import UUID
from datetime import datetime
from typing import Optional
from decimal import Decimal


class RawMaterialBase(BaseModel):
    name: str
    unit: str = "pcs"
    current_price: Decimal = Field(default=0.0, decimal_places=2)
    stock: Decimal = Field(default=0.0, decimal_places=2)


class RawMaterialCreate(RawMaterialBase):
    pass


class RawMaterialUpdate(BaseModel):
    name: Optional[str] = None
    unit: Optional[str] = None
    current_price: Optional[Decimal] = None
    stock: Optional[Decimal] = None


class RawMaterialResponse(RawMaterialBase):
    id: UUID
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class RawMaterialPriceHistoryResponse(BaseModel):
    id: UUID
    material_id: UUID
    price: Decimal
    source: str
    recorded_at: datetime

    model_config = ConfigDict(from_attributes=True)
