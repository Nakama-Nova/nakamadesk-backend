from datetime import datetime
from decimal import Decimal
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class BOMBase(BaseModel):
    """
    Base attributes for a Bill of Materials entry.
    """

    material_id: UUID
    required_qty: Decimal = Field(..., gt=0, decimal_places=4)
    wastage_pct: Decimal = Field(default=Decimal("0.00"), decimal_places=4)


class BOMCreate(BOMBase):
    """
    Schema for defining a new BOM relationship for an item.
    """

    item_id: UUID


class BOMUpdate(BaseModel):
    """
    Schema for adjusting existing BOM quantities or wastage.
    """

    required_qty: Optional[Decimal] = None
    wastage_pct: Optional[Decimal] = None


class BOMResponse(BOMBase):
    """
    Detailed BOM record with audit timestamps and material metadata.
    """

    id: UUID
    item_id: UUID
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    # Optional material info for display
    material_name: Optional[str] = None
    material_unit: Optional[str] = None
    material_price: Optional[Decimal] = None

    model_config = ConfigDict(from_attributes=True)


class BOMCostResponse(BaseModel):
    """
    Aggregated cost analysis for an item's production based on its BOM.
    """

    item_id: UUID
    material_cost: Decimal
    total_cost: Decimal
    entries: List[BOMResponse] = []
