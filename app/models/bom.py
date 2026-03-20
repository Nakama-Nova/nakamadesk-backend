from sqlalchemy import Column, Numeric, ForeignKey, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
import uuid

from app.db.base import Base


class BillOfMaterials(Base):
    __tablename__ = "bill_of_materials"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("gen_random_uuid()"))
    item_id = Column(UUID(as_uuid=True), ForeignKey("items.id"), index=True, nullable=False)
    material_id = Column(UUID(as_uuid=True), ForeignKey("raw_materials.id"), nullable=False)
    required_qty = Column(Numeric(10, 2), nullable=False)
    wastage_pct = Column(Numeric(10, 2), default=0.0)

    item = relationship("Item", back_populates="bom_entries")
    material = relationship("RawMaterial")
