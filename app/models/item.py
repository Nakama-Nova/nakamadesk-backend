from sqlalchemy import (
    Column,
    Float,
    String,
    Text,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    text,
    Numeric,
    func,
)
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.postgresql import UUID
from datetime import datetime, timezone
import uuid

from app.db.base import Base


class Item(Base):
    __tablename__ = "items"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    sku = Column(String, unique=True, index=True, nullable=False)
    name = Column(String, index=True, nullable=False)
    description = Column(Text, nullable=True)
    category_id = Column(UUID(as_uuid=True), ForeignKey("categories.id"), nullable=True)
    brand_id = Column(UUID(as_uuid=True), ForeignKey("brands.id"), nullable=True)
    unit = Column(String, default="pcs")
    purchase_price = Column(Numeric(10, 2), default=0.0)
    selling_price = Column(Numeric(10, 2), default=0.0)
    gst_percent = Column(Numeric(10, 2), default=0.0)
    hsn_code = Column(String, index=True)
    current_stock = Column(Integer, default=0)
    min_stock = Column(Integer, default=5)
    production_cost = Column(Numeric(10, 2), default=0.0)
    image_url = Column(String, nullable=True)
    is_active = Column(Boolean, default=True)
    version_id = Column(
        Integer, default=1, server_default="1", nullable=False
    )  # For optimistic locking
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    bom_entries = relationship(
        "BillOfMaterials", back_populates="item", cascade="all, delete-orphan"
    )

    __mapper_args__ = {"version_id_col": version_id}
