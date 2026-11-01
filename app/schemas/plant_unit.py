from datetime import datetime
from typing import Annotated, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

Latitude = Annotated[float, Field(ge=-90, le=90, allow_inf_nan=False)]
Longitude = Annotated[float, Field(ge=-180, le=180, allow_inf_nan=False)]


class PlantUnitIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    code: str = Field(min_length=1, max_length=10)
    latitude: Latitude
    longitude: Longitude
    radius_m: float = Field(gt=0, le=5000, allow_inf_nan=False)
    is_active: bool = True
    # plant (default) or shop. Left out on an update, the stored value is kept.
    kind: Optional[Literal["plant", "shop"]] = None
    has_till: Optional[bool] = None

    @field_validator("name", "code")
    @classmethod
    def strip(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("cannot be blank")
        return v

    @field_validator("code")
    @classmethod
    def upper(cls, v: str) -> str:
        return v.upper()


class PlantUnitOut(PlantUnitIn):
    id: UUID
    kind: Literal["plant", "shop"] = "plant"
    has_till: bool = False
    created_at: Optional[datetime] = None
    model_config = ConfigDict(from_attributes=True)


class GeoAttendanceIn(BaseModel):
    """A manager marking one person present, with the manager's phone position."""

    user_id: UUID
    status: str = "present"  # present or half-day
    latitude: Latitude
    longitude: Longitude
    accuracy_m: float = Field(ge=0, allow_inf_nan=False)
    client_id: Optional[UUID] = None  # idempotency, as on POST /attendance/

    @field_validator("status")
    @classmethod
    def status_ok(cls, v: str) -> str:
        if v not in ("present", "half-day"):
            raise ValueError("status must be present or half-day")
        return v
