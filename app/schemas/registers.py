"""Schemas for the machine and tool registers."""

from datetime import date, datetime
from decimal import Decimal
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

Money = Field(default=None, ge=0, max_digits=12, decimal_places=2)


def _clean(v: Optional[str]) -> Optional[str]:
    return v.strip() if isinstance(v, str) else v


# ----------------------------------------------------------------------- machines
class MachineIn(BaseModel):
    tag: str = Field(min_length=1, max_length=30)
    name: str = Field(min_length=1, max_length=100)
    make_model: Optional[str] = Field(default=None, max_length=100)
    serial_number: Optional[str] = Field(default=None, max_length=60)
    location_id: Optional[UUID] = None
    purchase_date: Optional[date] = None
    purchase_cost: Optional[Decimal] = Money
    useful_life_years: Optional[int] = Field(default=None, ge=1, le=50)
    salvage_value: Decimal = Field(
        default=Decimal("0"), ge=0, max_digits=12, decimal_places=2
    )
    maintenance_every_days: Optional[int] = Field(default=None, ge=1, le=3650)
    notes: Optional[str] = Field(default=None, max_length=1000)

    @field_validator("tag", "name")
    @classmethod
    def not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("cannot be blank")
        return v


class MachineUpdate(MachineIn):
    # active or retired. "down" is set by starting downtime, not here.
    status: Optional[Literal["active", "retired"]] = None


class MachineOut(BaseModel):
    id: UUID
    tag: str
    name: str
    make_model: Optional[str] = None
    serial_number: Optional[str] = None
    location_id: Optional[UUID] = None
    purchase_date: Optional[date] = None
    purchase_cost: Optional[Decimal] = None
    useful_life_years: Optional[int] = None
    salvage_value: Decimal
    maintenance_every_days: Optional[int] = None
    status: str
    notes: Optional[str] = None
    # worked out, not stored
    accumulated_depreciation: Optional[Decimal] = None
    book_value: Optional[Decimal] = None
    maintenance_spent: Decimal = Decimal("0.00")
    service_due_on: Optional[date] = None
    service_overdue: bool = False
    model_config = ConfigDict(from_attributes=True)


class MaintenanceIn(BaseModel):
    kind: Literal["scheduled", "repair"]
    performed_on: date
    cost: Decimal = Field(default=Decimal("0"), ge=0, max_digits=12, decimal_places=2)
    description: Optional[str] = Field(default=None, max_length=1000)
    done_by: Optional[str] = Field(default=None, max_length=100)


class MaintenanceOut(MaintenanceIn):
    id: UUID
    machine_id: UUID
    model_config = ConfigDict(from_attributes=True)


class DowntimeStart(BaseModel):
    reason: Optional[str] = Field(default=None, max_length=300)


class DowntimeOut(BaseModel):
    id: UUID
    machine_id: UUID
    started_at: datetime
    ended_at: Optional[datetime] = None
    reason: Optional[str] = None
    model_config = ConfigDict(from_attributes=True)


# --------------------------------------------------------------------------- tools
Condition = Literal["new", "good", "worn", "damaged"]


class ToolIn(BaseModel):
    tag: str = Field(min_length=1, max_length=30)
    name: str = Field(min_length=1, max_length=100)
    category: Optional[str] = Field(default=None, max_length=50)
    location_id: Optional[UUID] = None
    purchase_date: Optional[date] = None
    purchase_cost: Optional[Decimal] = Money
    condition: Condition = "good"
    notes: Optional[str] = Field(default=None, max_length=1000)

    @field_validator("tag", "name")
    @classmethod
    def not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("cannot be blank")
        return v


class ToolUpdate(ToolIn):
    # available or retired. issued / broken / lost follow from issuing and returning.
    status: Optional[Literal["available", "retired"]] = None


class ToolOut(BaseModel):
    id: UUID
    tag: str
    name: str
    category: Optional[str] = None
    location_id: Optional[UUID] = None
    purchase_date: Optional[date] = None
    purchase_cost: Optional[Decimal] = None
    status: str
    condition: str
    notes: Optional[str] = None
    held_by: Optional[UUID] = None  # who has it now, when issued
    model_config = ConfigDict(from_attributes=True)


class IssueIn(BaseModel):
    user_id: UUID
    notes: Optional[str] = Field(default=None, max_length=300)


class ReturnIn(BaseModel):
    outcome: Literal["ok", "damaged", "lost"] = "ok"
    # what state it came back in; defaults to what it went out in (ok) or damaged
    condition_in: Optional[Condition] = None
    notes: Optional[str] = Field(default=None, max_length=300)


class IssueOut(BaseModel):
    id: UUID
    tool_id: UUID
    tool_tag: Optional[str] = None
    tool_name: Optional[str] = None
    user_id: UUID
    user_name: Optional[str] = None
    issued_at: datetime
    condition_out: str
    returned_at: Optional[datetime] = None
    outcome: Optional[str] = None
    condition_in: Optional[str] = None
    notes: Optional[str] = None
