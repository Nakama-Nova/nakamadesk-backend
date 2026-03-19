from sqlalchemy import Column, Float, ForeignKey, Integer, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
import uuid

from app.db.base import Base


class SaleItem(Base):
    __tablename__ = "sale_items"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("gen_random_uuid()"))
    sale_id = Column(UUID(as_uuid=True), ForeignKey("sales.id"))
    item_id = Column(UUID(as_uuid=True), ForeignKey("items.id"))
    quantity = Column(Integer, nullable=False)
    price_at_sale = Column(Float, nullable=False)
    gst_percent = Column(Float, default=0.0)
    cgst_amount = Column(Float, default=0.0)
    sgst_amount = Column(Float, default=0.0)
    total_price = Column(Float, default=0.0)

    sale = relationship("Sale", back_populates="items")
    item = relationship("Item")
