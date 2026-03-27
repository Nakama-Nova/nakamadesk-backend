import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Numeric, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.db.base import Base


class BillOfMaterials(Base):
    """
    SQLAlchemy model representing the Bill of Materials (BOM) for an inventory item.

    Defines the relationship between a finished product and its raw material components,
    specifying the quantity and estimated wastage for production cost calculations.
    """

    __tablename__ = "bill_of_materials"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    item_id = Column(
        UUID(as_uuid=True), ForeignKey("items.id"), index=True, nullable=False
    )
    material_id = Column(
        UUID(as_uuid=True), ForeignKey("raw_materials.id"), index=True, nullable=False
    )
    required_qty = Column(Numeric(10, 4), nullable=False)
    wastage_pct = Column(Numeric(10, 4), default=0.0)

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    item = relationship("Item", back_populates="bom_entries")
    material = relationship("RawMaterial")
