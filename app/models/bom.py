from sqlalchemy import Column, Float, ForeignKey, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
import uuid

from app.db.base import Base


class BillOfMaterial(Base):
    __tablename__ = "bom"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("gen_random_uuid()"))
    item_id = Column(UUID(as_uuid=True), ForeignKey("items.id"), index=True)
    material_id = Column(UUID(as_uuid=True), ForeignKey("raw_materials.id"))
    quantity = Column(Float, nullable=False)
    wastage_pct = Column(Float, default=0.0)

    item = relationship("Item")
    material = relationship("RawMaterial")
