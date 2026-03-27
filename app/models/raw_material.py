from sqlalchemy import Column, Numeric, String, DateTime, text, CheckConstraint, func
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.postgresql import UUID
from datetime import datetime, timezone
import uuid

from app.db.base import Base


class RawMaterial(Base):
    """
    SQLAlchemy model for raw materials.

    Represents bulk resources (e.g., Timber) used in the manufacturing
    of finished inventory items. Tracks stock levels and current pricing.
    """

    __tablename__ = "raw_materials"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    name = Column(String, index=True, nullable=False, unique=True)
    unit = Column(String, default="pcs")  # CFT, KG, LTR, etc.
    current_price = Column(Numeric(10, 2), default=0.0)
    stock = Column(Numeric(10, 2), default=0.0)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    __table_args__ = (CheckConstraint("stock >= 0", name="check_stock_positive"),)

    bom_entries = relationship("BillOfMaterials", back_populates="material")
