from sqlalchemy import Column, Float, ForeignKey, Integer, text, Numeric, func, DateTime
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
import uuid
from datetime import datetime, timezone

from app.db.base import Base


class SaleItem(Base):
    __tablename__ = "sale_items"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("gen_random_uuid()"))
    sale_id = Column(UUID(as_uuid=True), ForeignKey("sales.id"), index=True)
    item_id = Column(UUID(as_uuid=True), ForeignKey("items.id"), index=True)
    quantity = Column(Integer, nullable=False)
    price_at_sale = Column(Numeric(10, 2), nullable=False)
    gst_percent = Column(Numeric(10, 2), default=0.0)
    cgst_amount = Column(Numeric(10, 2), default=0.0)
    sgst_amount = Column(Numeric(10, 2), default=0.0)
    total_price = Column(Numeric(10, 2), default=0.0)
    
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    sale = relationship("Sale", back_populates="items")
    item = relationship("Item")
