import uuid

from sqlalchemy import (
    CheckConstraint,
    Column,
    ForeignKey,
    Numeric,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID

from app.db.base import Base


class StockLevel(Base):
    """
    How much of one item or raw material sits at one location.

    items.current_stock and raw_materials.stock stay the TOTAL across the business, so
    everything that reads them keeps working. These rows say where part of that total
    is. Whatever is not placed at a location yet is "unassigned": total minus the sum
    of these rows (see stock_location_service.unassigned). A level can go negative when
    something is sold at a location before it was transferred there; a transfer fixes it.
    """

    __tablename__ = "stock_levels"
    __table_args__ = (
        CheckConstraint(
            "num_nonnulls(item_id, raw_material_id) = 1", name="ck_stock_levels_one_of"
        ),
        UniqueConstraint("location_id", "item_id", name="uq_stock_levels_item"),
        UniqueConstraint(
            "location_id", "raw_material_id", name="uq_stock_levels_material"
        ),
    )

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    location_id = Column(
        UUID(as_uuid=True), ForeignKey("plant_units.id"), nullable=False, index=True
    )
    item_id = Column(UUID(as_uuid=True), ForeignKey("items.id"), nullable=True)
    raw_material_id = Column(
        UUID(as_uuid=True), ForeignKey("raw_materials.id"), nullable=True
    )
    quantity = Column(Numeric(12, 4), nullable=False, default=0)
