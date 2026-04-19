"""
SQLAlchemy model for Production Material Allocations.

Records which raw materials were pulled from stock when a production
job was started. This is the link between a job and the raw materials
it consumed, and the source record for `raw_consumed` inventory movements.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Numeric, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.db.base import Base


class ProductionMaterialAllocation(Base):
    """
    Records raw material consumption for a production job.

    Created atomically when a job transitions to 'in_progress'.
    One record per material required by the BOM.
    """

    __tablename__ = "production_material_allocations"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )

    job_id = Column(
        UUID(as_uuid=True),
        ForeignKey("production_jobs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    material_id = Column(
        UUID(as_uuid=True),
        ForeignKey("raw_materials.id"),
        nullable=False,
        index=True,
    )

    # Planned vs actual (wastage may differ)
    allocated_qty = Column(Numeric(10, 4), nullable=False)
    consumed_qty = Column(Numeric(10, 4), default=0)
    scrap_qty = Column(Numeric(10, 4), default=0)

    allocated_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    allocated_at = Column(
        DateTime, nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    created_at = Column(
        DateTime, nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    # Relationships
    job = relationship("ProductionJob", back_populates="material_allocations")
    material = relationship("RawMaterial", foreign_keys=[material_id])
    allocator = relationship("User", foreign_keys=[allocated_by])
