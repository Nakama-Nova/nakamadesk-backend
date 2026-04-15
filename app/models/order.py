"""
SQLAlchemy model for customer orders.

Separates the concept of an 'order' from a 'sales invoice'.
An order tracks the full lifecycle of a request from the customer --
including custom/bespoke work -- before an invoice is ever generated.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Column,
    Date,
    DateTime,
    ForeignKey,
    Numeric,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship

from app.db.base import Base


class Order(Base):
    """
    SQLAlchemy model for customer orders.

    An order is the primary business document for Vriksha Studio. It covers
    both standard orders (known catalogue items) and custom orders (bespoke
    furniture or temple art with specific design requirements).

    Lifecycle:
        draft -> confirmed -> in_production -> ready -> delivered -> cancelled
    """

    __tablename__ = "orders"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    order_number = Column(
        String, unique=True, index=True, nullable=False
    )  # e.g. ORD-2024-0001

    customer_id = Column(
        UUID(as_uuid=True), ForeignKey("customers.id"), nullable=True, index=True
    )
    order_type = Column(
        String, nullable=False, default="standard"
    )  # 'standard' | 'custom'
    status = Column(
        String, nullable=False, default="draft"
    )  # draft|confirmed|in_production|ready|delivered|cancelled

    # Custom order -- flexible spec bag (dimensions, wood type, motif, etc.)
    custom_specs = Column(JSONB, nullable=True)
    reference_image_url = Column(String, nullable=True)

    # Financials
    estimated_amount = Column(Numeric(12, 2), default=0)
    advance_paid = Column(Numeric(12, 2), default=0)
    final_amount = Column(Numeric(12, 2), default=0)

    # Dates
    order_date = Column(
        DateTime, nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    expected_delivery = Column(Date, nullable=True)
    delivered_at = Column(DateTime, nullable=True)

    # Linked sale invoice (set on delivery/billing -- Day 3+)
    sale_id = Column(UUID(as_uuid=True), ForeignKey("sales.id"), nullable=True)

    # Audit
    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    notes = Column(Text, nullable=True)

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    # Relationships
    customer = relationship("Customer", foreign_keys=[customer_id])
    creator = relationship("User", foreign_keys=[created_by])
    items = relationship(
        "OrderItem", back_populates="order", cascade="all, delete-orphan"
    )
    production_jobs = relationship("ProductionJob", back_populates="order")
