import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Integer, Numeric, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.db.base import Base


class SaleItem(Base):
    """
    SQLAlchemy model for individual items in a sale.

    Links inventory items to a sale record and captures the static price
    and tax amounts at the time of the transaction.
    """

    __tablename__ = "sale_items"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
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
