"""
SQLAlchemy model for worker-to-production-job assignments.

Tracks which workers (carpenters, sculptors, polishers) are assigned
to a given production job and their functional role within it.
A unique constraint ensures one active assignment per worker per job.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.db.base import Base


class JobWorkerAssignment(Base):
    """
    SQLAlchemy model for assigning a worker to a production job.

    Maps workers (users with role 'achari', 'worker', etc.) to specific
    production jobs with a designated craft role. A composite unique
    constraint prevents double-assigning the same worker to the same job.
    """

    __tablename__ = "job_worker_assignments"

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
    worker_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=False,
        index=True,
    )

    # Functional role on this specific job
    # e.g. 'carpenter', 'sculptor', 'polisher', 'helper'
    role = Column(String, nullable=True)

    assigned_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    # Soft-remove: set removed_at instead of deleting so history is preserved
    removed_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    # Relationships
    job = relationship("ProductionJob", back_populates="worker_assignments")
    worker = relationship("User", foreign_keys=[worker_id])

    __table_args__ = (UniqueConstraint("job_id", "worker_id", name="uq_job_worker"),)
