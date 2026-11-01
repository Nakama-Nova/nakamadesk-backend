import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Float,
    String,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID

from app.db.base import Base


class PlantUnit(Base):
    """
    One working area of the workshop (Manufacturing/Production, Painting/Processing,
    QC), drawn as a circle: a centre point and a radius in metres.

    Attendance marked from a phone is matched to the unit whose circle contains
    the reported GPS position. Units are switched off (is_active), not deleted,
    because past attendance rows point at them.

    This table is also the list of business locations: stock levels, stock movements
    and sales point at it. The Shop is a row with kind="shop". It is not renamed to
    "locations" yet because the previous release still uses this name.
    """

    __tablename__ = "plant_units"
    __table_args__ = (
        CheckConstraint("latitude BETWEEN -90 AND 90", name="ck_plant_units_lat"),
        CheckConstraint("longitude BETWEEN -180 AND 180", name="ck_plant_units_lon"),
        CheckConstraint("radius_m > 0", name="ck_plant_units_radius"),
        CheckConstraint("kind IN ('plant', 'shop')", name="ck_plant_units_kind"),
    )

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    name = Column(String, unique=True, nullable=False)
    code = Column(String, unique=True, nullable=False)  # short, upper case: "MFG"
    latitude = Column(Float, nullable=False)  # centre
    longitude = Column(Float, nullable=False)
    radius_m = Column(Float, nullable=False)
    is_active = Column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    # plant: where things are made. shop: where finished goods are sold.
    kind = Column(String, nullable=False, default="plant", server_default="plant")
    has_till = Column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())
