"""
SQLAlchemy model for Inventory Movements.

Every stock change — purchase receipt, raw material consumption,
finished goods addition, sale deduction, or manual adjustment —
is recorded here as an immutable audit entry.

This is the core of the inventory engine: the source of truth for
'what happened and why' for any stock quantity change.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Numeric, String, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.db.base import Base


class InventoryMovement(Base):
    """
    Immutable ledger entry for every stock quantity change.

    Design rules:
    - quantity > 0 means stock ADDED  (raw_in, finished_in)
    - quantity < 0 means stock REMOVED (raw_consumed, finished_out)
    - Exactly one of raw_material_id / item_id must be set
    - reference_type + reference_id forms a polymorphic FK to the
      source record (purchase, production_job, sale, etc.)
    """

    __tablename__ = "inventory_movements"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )

    # What kind of movement
    movement_type = Column(
        String, nullable=False
    )  # raw_in | raw_consumed | finished_in | finished_out | adjustment

    # Polymorphic reference to the source record
    reference_type = Column(
        String, nullable=True
    )  # purchase | production_job | sale | manual
    reference_id = Column(UUID(as_uuid=True), nullable=True)

    # One of these must be set, never both
    raw_material_id = Column(
        UUID(as_uuid=True), ForeignKey("raw_materials.id"), nullable=True, index=True
    )
    item_id = Column(
        UUID(as_uuid=True), ForeignKey("items.id"), nullable=True, index=True
    )

    # Positive = in, Negative = out
    quantity = Column(Numeric(10, 4), nullable=False)
    unit_cost = Column(Numeric(10, 2), nullable=True)

    notes = Column(Text, nullable=True)
    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    created_at = Column(
        DateTime, nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    # Relationships (for easy joins in queries)
    raw_material = relationship("RawMaterial", foreign_keys=[raw_material_id])
    item = relationship("Item", foreign_keys=[item_id])
    creator = relationship("User", foreign_keys=[created_by])
