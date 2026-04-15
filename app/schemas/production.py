"""
Pydantic schemas for Production Job and Worker Assignment endpoints.

Follows the same Base → Create → Response pattern used throughout the
codebase (see schemas/sale.py, schemas/raw_material.py for reference).
"""

from datetime import date, datetime
from decimal import Decimal
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Worker Assignment schemas
# ---------------------------------------------------------------------------


class WorkerAssignmentCreate(BaseModel):
    """
    Schema for assigning a worker to a production job.

    role is the craft function on this specific job (e.g. 'carpenter',
    'sculptor', 'polisher', 'helper'). It differs from the user's system
    role (achari, worker) which describes their overall employment level.
    """

    worker_id: UUID
    role: Optional[str] = None  # 'carpenter' | 'sculptor' | 'polisher' | 'helper'


class WorkerAssignmentResponse(BaseModel):
    """Data Transfer Object for a job-worker assignment record."""

    id: UUID
    job_id: UUID
    worker_id: UUID
    role: Optional[str] = None
    assigned_at: datetime
    removed_at: Optional[datetime] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------------------
# Production Job schemas
# ---------------------------------------------------------------------------


class ProductionJobCreate(BaseModel):
    """
    Schema for creating a new manufacturing/production job.

    Can be linked to a customer order (order_id) or standalone
    (order_id=None) for stock replenishment production.

    Either item_id (catalogue product) or custom_desc (bespoke) must
    be provided to describe what is being manufactured.
    """

    order_id: Optional[UUID] = None
    item_id: Optional[UUID] = None
    custom_desc: Optional[str] = None

    target_quantity: int = Field(default=1, ge=1)
    expected_by: Optional[date] = None
    assigned_to: Optional[UUID] = None  # Achari / supervisor UUID
    notes: Optional[str] = None


class ProductionJobStatusUpdate(BaseModel):
    """Schema for updating the status of a production job."""

    status: str = Field(pattern="^(pending|in_progress|completed|cancelled)$")
    notes: Optional[str] = None


class ProductionJobResponse(BaseModel):
    """
    Data Transfer Object for a production job, including its worker
    assignments and progress summary.
    """

    id: UUID
    job_number: str
    order_id: Optional[UUID] = None
    item_id: Optional[UUID] = None
    custom_desc: Optional[str] = None

    target_quantity: int
    produced_quantity: Decimal

    status: str
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    expected_by: Optional[date] = None

    assigned_to: Optional[UUID] = None
    created_by: UUID
    notes: Optional[str] = None

    worker_assignments: List[WorkerAssignmentResponse] = []

    created_at: datetime
    updated_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)
