import uuid

from sqlalchemy import Column, ForeignKey, Numeric, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

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
    item_id = Column(UUID(as_uuid=True), ForeignKey("items.id"), nullable=True)
    # Day 2: raw material purchases (teak, neem, etc.)
    raw_material_id = Column(
        UUID(as_uuid=True), ForeignKey("raw_materials.id"), nullable=True
    )
    quantity = Column(
        Numeric(10, 4), nullable=False
    )  # changed to Numeric for fractional qty
    unit_price = Column(Numeric(10, 2), nullable=False)
    gst_percent = Column(Numeric(10, 2), default=0.0)
    line_total = Column(Numeric(10, 2), default=0.0)

    purchase = relationship("Purchase", back_populates="items")
    item = relationship("Item", foreign_keys=[item_id])
    raw_material = relationship("RawMaterial", foreign_keys=[raw_material_id])
