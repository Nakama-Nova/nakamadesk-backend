"""
SQLAlchemy model for individual line items within an order.

Each row represents one product (or custom item) within a customer order.
For standard items, item_id links to the existing items table.
For custom/bespoke items, item_id is NULL and item_name captures the description.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.db.base import Base


class OrderItem(Base):
    """
    SQLAlchemy model for a line item within a customer order.

    Supports both standard catalogue products (item_id set) and
    custom/bespoke items (item_id is NULL, item_name describes the piece).
    """

    __tablename__ = "order_items"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    order_id = Column(
        UUID(as_uuid=True),
        ForeignKey("orders.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # For standard items -- FK to the catalogue
    item_id = Column(UUID(as_uuid=True), ForeignKey("items.id"), nullable=True)

    # For custom items -- explicit name (required when item_id is NULL)
    item_name = Column(String, nullable=True)

    quantity = Column(Integer, nullable=False, default=1)
    unit_price = Column(Numeric(12, 2), nullable=False, default=0)
    notes = Column(Text, nullable=True)

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    # Relationships
    order = relationship("Order", back_populates="items")
    item = relationship("Item", foreign_keys=[item_id])
