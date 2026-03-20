from pydantic import BaseModel, ConfigDict, Field
from uuid import UUID
from typing import List, Optional
from decimal import Decimal


class BOMBase(BaseModel):
    material_id: UUID
    required_qty: Decimal = Field(..., decimal_places=4)
    wastage_pct: Decimal = Field(default=Decimal("0.00"), decimal_places=4)


class BOMCreate(BOMBase):
    item_id: UUID


class BOMUpdate(BaseModel):
    required_qty: Optional[Decimal] = None
    wastage_pct: Optional[Decimal] = None


class BOMResponse(BOMBase):
    id: UUID
    item_id: UUID
    
    # Optional material info for display
    material_name: Optional[str] = None
    material_unit: Optional[str] = None
    material_price: Optional[Decimal] = None

    model_config = ConfigDict(from_attributes=True)


class BOMCostResponse(BaseModel):
    item_id: UUID
    material_cost: Decimal
    total_cost: Decimal
    entries: List[BOMResponse] = []
