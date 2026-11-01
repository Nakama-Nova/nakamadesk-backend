"""
Which plant unit is a phone standing in?

The phone's GPS position is untrusted client input: it is range-checked here, capped
on its reported accuracy, and the unit is always chosen by the server. A client can
send a position, never a unit.
"""

import math
from dataclasses import dataclass
from typing import Optional
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.plant_unit import PlantUnit

ON_SITE = "on_site"
OFF_SITE = "off_site"

_EARTH_RADIUS_M = 6_371_000


class GeoError(ValueError):
    """The reported position cannot be used (out of range, or too vague)."""


@dataclass(frozen=True)
class Location:
    """A checked GPS fix and where it landed. plant_unit_id is None when off site."""

    latitude: float
    longitude: float
    accuracy_m: float
    plant_unit_id: Optional[UUID]
    status: str  # ON_SITE or OFF_SITE

    def as_columns(self) -> dict:
        return {
            "latitude": self.latitude,
            "longitude": self.longitude,
            "accuracy_m": self.accuracy_m,
            "plant_unit_id": self.plant_unit_id,
            "location_status": self.status,
        }


def distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle (haversine) distance in metres; plenty exact at workshop scale."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = (
        math.sin((p2 - p1) / 2) ** 2
        + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    )
    return 2 * _EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(a)))


def check_fix(latitude, longitude, accuracy_m) -> tuple:
    """Return (lat, lon, accuracy) as floats, or raise GeoError."""
    try:
        lat, lon, acc = float(latitude), float(longitude), float(accuracy_m)
    except (TypeError, ValueError):
        raise GeoError("latitude, longitude and accuracy_m must be numbers") from None
    if not all(math.isfinite(v) for v in (lat, lon, acc)):
        raise GeoError("latitude, longitude and accuracy_m must be finite numbers")
    if not -90 <= lat <= 90:
        raise GeoError("latitude must be between -90 and 90")
    if not -180 <= lon <= 180:
        raise GeoError("longitude must be between -180 and 180")
    if acc < 0:
        raise GeoError("accuracy_m cannot be negative")
    if acc > settings.GPS_MAX_ACCURACY_M:
        raise GeoError(
            f"GPS accuracy is {acc:.0f} m; it must be {settings.GPS_MAX_ACCURACY_M:.0f} m"
            " or better. Wait for a better fix and try again."
        )
    return lat, lon, acc


def find_unit(session: Session, lat: float, lon: float) -> Optional[PlantUnit]:
    """
    The active unit whose circle contains the point. Where circles overlap, the
    unit whose centre is nearest wins (ties go to the lower code), so the same
    point always gives the same unit.
    """
    best, best_key = None, None
    for unit in session.query(PlantUnit).filter(PlantUnit.is_active.is_(True)):
        d = distance_m(lat, lon, unit.latitude, unit.longitude)
        if d <= unit.radius_m and (best_key is None or (d, unit.code) < best_key):
            best, best_key = unit, (d, unit.code)
    return best


def locate(session: Session, latitude, longitude, accuracy_m) -> Location:
    """Check a reported fix and match it to a unit. Raises GeoError if unusable."""
    lat, lon, acc = check_fix(latitude, longitude, accuracy_m)
    unit = find_unit(session, lat, lon)
    if unit is None:
        return Location(lat, lon, acc, None, OFF_SITE)
    return Location(lat, lon, acc, unit.id, ON_SITE)
