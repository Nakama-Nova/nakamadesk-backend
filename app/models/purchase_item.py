from sqlalchemy import Column, ForeignKey, Integer, text, Numeric
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
import uuid

from app.db.base import Base


class PurchaseItem(Base):
    """
    SQLAlchemy model for individual items in a purchase.

    Links specific inventory items to a purchase record and stores
    the unit price and quantity at the time of procurement.
    """

    __tablename__ = "purchase_items"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    purchase_id = Column(UUID(as_uuid=True), ForeignKey("purchases.id"))
    item_id = Column(UUID(as_uuid=True), ForeignKey("items.id"))
    quantity = Column(Integer, nullable=False)
    unit_price = Column(Numeric(10, 2), nullable=False)
    gst_percent = Column(Numeric(10, 2), default=0.0)
    line_total = Column(Numeric(10, 2), default=0.0)

    purchase = relationship("Purchase", back_populates="items")
    item = relationship("Item")
