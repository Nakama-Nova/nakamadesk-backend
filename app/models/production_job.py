"""
SQLAlchemy model for production/manufacturing jobs.

A ProductionJob represents a discrete manufacturing task -- the activity
of turning raw materials into a finished product. It is the central
coordinating entity between orders, raw material consumption (Day 2),
and worker assignments.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Column,
    Date,
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


class ProductionJob(Base):
    """
    SQLAlchemy model for a manufacturing/production job.

    Tracks the full lifecycle of turning raw materials into a finished
    product. Can be linked to a customer order or standalone (for stock
    replenishment).

    Lifecycle:
        pending -> in_progress -> completed | cancelled
    """

    __tablename__ = "production_jobs"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    job_number = Column(
        String, unique=True, index=True, nullable=False
    )  # e.g. JOB-2024-0001

    # Optional -- NULL means standalone stock production (not for a specific order)
    order_id = Column(
        UUID(as_uuid=True), ForeignKey("orders.id"), nullable=True, index=True
    )

    # The finished product being manufactured
    # NULL for fully custom one-off pieces that aren't in the catalogue
    item_id = Column(UUID(as_uuid=True), ForeignKey("items.id"), nullable=True)
    custom_desc = Column(String, nullable=True)  # Description for non-catalogue items

    # Quantities
    target_quantity = Column(Integer, nullable=False, default=1)
    produced_quantity = Column(
        Numeric(10, 4), nullable=False, default=0
    )  # Numeric for partial units (e.g. 1.5 panels)

    # Status
    status = Column(
        String, nullable=False, default="pending"
    )  # pending|in_progress|completed|cancelled

    # Dates
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    expected_by = Column(Date, nullable=True)

    # Responsibility -- Achari or senior carpenter who owns this job
    assigned_to = Column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True, index=True
    )
    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    notes = Column(Text, nullable=True)

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    # Relationships
    order = relationship("Order", back_populates="production_jobs")
    item = relationship("Item", foreign_keys=[item_id])
    supervisor = relationship("User", foreign_keys=[assigned_to])
    creator = relationship("User", foreign_keys=[created_by])
    worker_assignments = relationship(
        "JobWorkerAssignment",
        back_populates="job",
        cascade="all, delete-orphan",
    )
    # Day 2: raw material allocations created when job is started
    material_allocations = relationship(
        "ProductionMaterialAllocation",
        back_populates="job",
        cascade="all, delete-orphan",
    )
