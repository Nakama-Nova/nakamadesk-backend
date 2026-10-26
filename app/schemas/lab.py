"""Schemas for the 3D Lab (staff only)."""

from typing import List, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class PartIn(BaseModel):
    """One placed piece. Sizes/positions are formulas of L (left-right), W (front-back), H (up), in mm."""

    name: str = Field(min_length=1, max_length=80)
    wood_type: Optional[str] = Field(default=None, max_length=60)
    raw_material_id: Optional[UUID] = None
    l_expr: str = Field(max_length=100)  # length (the long side)
    w_expr: str = Field(max_length=100)  # width
    t_expr: str = Field(max_length=100)  # thickness
    axis: Literal["x", "y", "z"] = "x"  # which way the length runs
    x_expr: str = Field(default="0", max_length=100)  # lowest corner
    y_expr: str = Field(default="0", max_length=100)
    z_expr: str = Field(default="0", max_length=100)
    assembly_step: int = Field(default=1, ge=1, le=50)
    joinery_note: Optional[str] = Field(default=None, max_length=300)
    model_asset_id: Optional[UUID] = None


class PartOut(PartIn):
    id: UUID

    model_config = ConfigDict(from_attributes=True)


class PartsIn(BaseModel):
    parts: List[PartIn] = Field(max_length=150)


class SizeIn(BaseModel):
    l: float = Field(ge=50, le=5000)  # noqa: E741
    w: float = Field(ge=50, le=5000)
    h: float = Field(ge=50, le=5000)


class PresetsIn(BaseModel):
    S: Optional[SizeIn] = None
    M: Optional[SizeIn] = None
    L: Optional[SizeIn] = None


class FurnitureIn(BaseModel):
    is_furniture: bool


class LinkIn(BaseModel):
    item_id: UUID
    mode: Literal["view", "edit"] = "view"


class LabItem(BaseModel):
    id: UUID
    name: str
    sku: str
    parts: int
    sizes: List[str]
