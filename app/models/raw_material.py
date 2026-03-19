from sqlalchemy import Column, Float, String, text
from sqlalchemy.dialects.postgresql import UUID
import uuid

from app.db.base import Base


class RawMaterial(Base):
    __tablename__ = "raw_materials"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("gen_random_uuid()"))
    name = Column(String, index=True, nullable=False)
    unit = Column(String, default="pcs") # CFT, KG, Ltr, etc.
    current_price = Column(Float, default=0.0)
    stock_quantity = Column(Float, default=0.0)
