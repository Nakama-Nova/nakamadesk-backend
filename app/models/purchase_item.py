from sqlalchemy import Column, Float, ForeignKey, Integer, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
import uuid

from app.db.base import Base


class PurchaseItem(Base):
    __tablename__ = "purchase_items"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("gen_random_uuid()"))
    purchase_id = Column(UUID(as_uuid=True), ForeignKey("purchases.id"))
    item_id = Column(UUID(as_uuid=True), ForeignKey("items.id"))
    quantity = Column(Integer, nullable=False)
    unit_price = Column(Float, nullable=False)
    gst_percent = Column(Float, default=0.0)
    line_total = Column(Float, default=0.0)

    purchase = relationship("Purchase", back_populates="items")
    item = relationship("Item")
