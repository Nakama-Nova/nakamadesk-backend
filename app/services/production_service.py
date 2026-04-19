"""
Service layer for Production Job management.

Handles creation of manufacturing jobs, worker assignment/removal,
job lifecycle transitions (start / complete), and raw material
consumption via the inventory movement engine.

Follows the same UoW (Unit of Work) pattern used in
sales_service.py and workforce_service.py.
"""

import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import List, Optional
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import joinedload

from app.models.bom import BillOfMaterials
from app.models.job_assignment import JobWorkerAssignment
from app.models.order import Order
from app.models.production_job import ProductionJob
from app.models.production_material_allocation import ProductionMaterialAllocation
from app.repositories.base import AbstractUnitOfWork
from app.schemas.production import ProductionJobCreate, WorkerAssignmentCreate
from app.services import inventory_service

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Job number generation
# ---------------------------------------------------------------------------


def _generate_job_number(uow: AbstractUnitOfWork) -> str:
    """
    Generate a sequential, human-readable job number.

    Format: JOB-YYYY-NNNN (e.g. JOB-2026-0001).

    Args:
        uow (AbstractUnitOfWork): Active unit of work.

    Returns:
        str: A unique job number string.
    """
    year = datetime.now(timezone.utc).year
    count = (
        uow.session.query(func.count(ProductionJob.id))
        .filter(ProductionJob.job_number.like(f"JOB-{year}-%"))
        .scalar()
        or 0
    )
    return f"JOB-{year}-{count + 1:04d}"


# ---------------------------------------------------------------------------
# Job lifecycle — Day 2: start & complete
# ---------------------------------------------------------------------------


def start_job(
    uow: AbstractUnitOfWork,
    job_id: UUID,
    started_by: UUID,
) -> ProductionJob:
    """
    Start a production job: consume raw materials per BOM and set status.

    Workflow:
      1. Validate job exists and is in 'pending' state.
      2. Fetch BOM entries for the job's item.
      3. For each BOM entry, call consume_raw_material() — which
         deducts stock and logs an inventory_movement (raw_consumed).
      4. Create a ProductionMaterialAllocation record per material.
      5. Set job.status = 'in_progress' and job.started_at = now.

    Everything happens inside one atomic transaction.

    Args:
        uow: Unit of Work (caller should NOT wrap in 'with uow').
        job_id: UUID of the production job to start.
        started_by: UUID of the authenticated user triggering start.

    Returns:
        ProductionJob: Updated job with in_progress status.

    Raises:
        HTTPException 404: If job not found.
        HTTPException 400: If job is not in 'pending' state.
        HTTPException 400: If any raw material has insufficient stock.
        HTTPException 400: If job's item has no BOM and item_id is set.
    """
    with uow:
        job = (
            uow.session.query(ProductionJob)
            .filter(ProductionJob.id == job_id)
            .with_for_update()
            .first()
        )
        if not job:
            raise HTTPException(status_code=404, detail="Production job not found")

        if job.status != "pending":
            raise HTTPException(
                status_code=400,
                detail=f"Job must be 'pending' to start. Current status: {job.status}",
            )

        # Only auto-consume from BOM if we have a catalogue item
        if job.item_id:
            bom_entries = (
                uow.session.query(BillOfMaterials)
                .filter(BillOfMaterials.item_id == job.item_id)
                .all()
            )

            if not bom_entries:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"No BOM found for item {job.item_id}. "
                        "Add materials to the BOM before starting production."
                    ),
                )

            for entry in bom_entries:
                # Calculate total quantity including wastage
                # e.g. required_qty=10, wastage_pct=5 → consume 10.5
                wastage_multiplier = Decimal("1") + (
                    (entry.wastage_pct or Decimal("0")) / Decimal("100")
                )
                qty_to_consume = (
                    entry.required_qty * wastage_multiplier * job.target_quantity
                )

                # This raises 400 if stock insufficient — rolls back whole job start
                inventory_service.consume_raw_material(
                    uow=uow,
                    material_id=entry.material_id,
                    quantity=qty_to_consume,
                    job_id=job_id,
                    created_by=started_by,
                )

                # Record the allocation
                allocation = ProductionMaterialAllocation(
                    job_id=job_id,
                    material_id=entry.material_id,
                    allocated_qty=qty_to_consume,
                    consumed_qty=qty_to_consume,
                    allocated_by=started_by,
                )
                uow.session.add(allocation)

        job.status = "in_progress"
        job.started_at = datetime.now(timezone.utc)
        uow.commit()

    logger.info(
        "Production job %s (%s) started by user %s",
        job.job_number,
        job_id,
        started_by,
    )
    return get_job(uow, job_id)


def complete_job(
    uow: AbstractUnitOfWork,
    job_id: UUID,
    produced_quantity: int,
    completed_by: UUID,
) -> ProductionJob:
    """
    Complete a production job: add finished goods to stock.

    Workflow:
      1. Validate job is 'in_progress'.
      2. Validate produced_quantity >= 1.
      3. Call add_finished_goods() for the job's item (if catalogue item).
      4. Set job.produced_quantity, job.status = 'completed', job.completed_at.

    Args:
        uow: Unit of Work.
        job_id: UUID of the production job to complete.
        produced_quantity: Number of finished units produced.
        completed_by: UUID of the user marking completion.

    Returns:
        ProductionJob: Updated job with completed status.

    Raises:
        HTTPException 404: If job not found.
        HTTPException 400: If job is not 'in_progress'.
        HTTPException 400: If produced_quantity < 1.
    """
    if produced_quantity < 1:
        raise HTTPException(
            status_code=400, detail="produced_quantity must be at least 1"
        )

    with uow:
        job = (
            uow.session.query(ProductionJob)
            .filter(ProductionJob.id == job_id)
            .with_for_update()
            .first()
        )
        if not job:
            raise HTTPException(status_code=404, detail="Production job not found")

        if job.status != "in_progress":
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Job must be 'in_progress' to complete. "
                    f"Current status: {job.status}"
                ),
            )

        # Add finished goods to catalogue item stock (only if catalogue item)
        if job.item_id:
            inventory_service.add_finished_goods(
                uow=uow,
                item_id=job.item_id,
                quantity=produced_quantity,
                job_id=job_id,
                created_by=completed_by,
            )

        job.produced_quantity = produced_quantity
        job.status = "completed"
        job.completed_at = datetime.now(timezone.utc)
        uow.commit()

    logger.info(
        "Production job %s (%s) completed. Produced: %d units.",
        job.job_number,
        job_id,
        produced_quantity,
    )
    return get_job(uow, job_id)


def create_job(
    uow: AbstractUnitOfWork,
    job_data: ProductionJobCreate,
    created_by: UUID,
) -> ProductionJob:
    """
    Create a new production/manufacturing job.

    Validates that the linked order (if any) exists, and that the
    item_id (if supplied) resolves to a catalogue item. Either item_id
    or custom_desc must be provided to identify what is being made.

    Args:
        uow (AbstractUnitOfWork): Unit of Work for database operations.
        job_data (ProductionJobCreate): Validated job payload.
        created_by (UUID): ID of the authenticated user creating the job.

    Returns:
        ProductionJob: The fully persisted job with assignments loaded.

    Raises:
        HTTPException 400: If neither item_id nor custom_desc is provided.
        HTTPException 404: If order_id / item_id / assigned_to does not exist.
    """
    # Must know what is being manufactured
    if not job_data.item_id and not job_data.custom_desc:
        raise HTTPException(
            status_code=400,
            detail=(
                "Either item_id (catalogue product) or "
                "custom_desc (bespoke item) is required"
            ),
        )

    with uow:
        # Validate order exists (if linked)
        if job_data.order_id:
            order = (
                uow.session.query(Order).filter(Order.id == job_data.order_id).first()
            )
            if not order:
                raise HTTPException(
                    status_code=404,
                    detail=f"Order {job_data.order_id} not found",
                )

        # Validate catalogue item (if referenced)
        if job_data.item_id:
            item = uow.items.get_by_id(job_data.item_id)
            if not item:
                raise HTTPException(
                    status_code=404,
                    detail=f"Item {job_data.item_id} not found",
                )

        # Validate supervisor/assignee exists (if supplied)
        if job_data.assigned_to:
            supervisor = uow.users.get_by_id(job_data.assigned_to)
            if not supervisor:
                raise HTTPException(
                    status_code=404,
                    detail=f"User (supervisor) {job_data.assigned_to} not found",
                )

        job_number = _generate_job_number(uow)

        new_job = ProductionJob(
            job_number=job_number,
            order_id=job_data.order_id,
            item_id=job_data.item_id,
            custom_desc=job_data.custom_desc,
            target_quantity=job_data.target_quantity,
            produced_quantity=0,
            status="pending",
            expected_by=job_data.expected_by,
            assigned_to=job_data.assigned_to,
            created_by=created_by,
            notes=job_data.notes,
        )
        uow.session.add(new_job)
        uow.commit()

    uow.refresh(new_job)
    logger.info(
        "Production job created: %s (ID: %s) by user %s",
        new_job.job_number,
        new_job.id,
        created_by,
    )
    return get_job(uow, new_job.id)


def get_job(uow: AbstractUnitOfWork, job_id: UUID) -> Optional[ProductionJob]:
    """
    Retrieve a production job by ID with worker assignments eagerly loaded.

    Args:
        uow (AbstractUnitOfWork): Unit of Work for database access.
        job_id (UUID): Unique ID of the production job.

    Returns:
        Optional[ProductionJob]: The job with assignments, or None.
    """
    return (
        uow.session.query(ProductionJob)
        .options(joinedload(ProductionJob.worker_assignments))
        .filter(ProductionJob.id == job_id)
        .first()
    )


def list_jobs(
    uow: AbstractUnitOfWork,
    limit: int = 50,
    offset: int = 0,
    status: Optional[str] = None,
    order_id: Optional[UUID] = None,
    assigned_to: Optional[UUID] = None,
) -> List[ProductionJob]:
    """
    List production jobs with optional filters and pagination.

    Args:
        uow (AbstractUnitOfWork): Unit of Work for database access.
        limit (int): Maximum records to return.
        offset (int): Records to skip.
        status (Optional[str]): Filter by job status.
        order_id (Optional[UUID]): Filter by linked order.
        assigned_to (Optional[UUID]): Filter by supervisor.

    Returns:
        List[ProductionJob]: Matching jobs with assignments loaded.
    """
    query = uow.session.query(ProductionJob).options(
        joinedload(ProductionJob.worker_assignments)
    )

    if status:
        query = query.filter(ProductionJob.status == status)
    if order_id:
        query = query.filter(ProductionJob.order_id == order_id)
    if assigned_to:
        query = query.filter(ProductionJob.assigned_to == assigned_to)

    return (
        query.order_by(ProductionJob.created_at.desc())
        .limit(limit)
        .offset(offset)
        .all()
    )


# ---------------------------------------------------------------------------
# Worker assignment
# ---------------------------------------------------------------------------


def assign_worker(
    uow: AbstractUnitOfWork,
    job_id: UUID,
    assignment_data: WorkerAssignmentCreate,
) -> JobWorkerAssignment:
    """
    Assign a worker to a production job.

    Validates that both job and worker exist. The unique constraint
    on (job_id, worker_id) guarantees idempotency -- if the same
    worker is assigned again after being removed, the old record is
    reactivated (removed_at cleared) rather than creating a duplicate.

    Args:
        uow (AbstractUnitOfWork): Unit of Work for database operations.
        job_id (UUID): Unique ID of the production job.
        assignment_data (WorkerAssignmentCreate): Worker and role details.

    Returns:
        JobWorkerAssignment: The created or reactivated assignment record.

    Raises:
        HTTPException 404: If job or worker does not exist.
        HTTPException 400: If worker is already actively assigned.
    """
    with uow:
        # Validate job
        job = (
            uow.session.query(ProductionJob).filter(ProductionJob.id == job_id).first()
        )
        if not job:
            raise HTTPException(status_code=404, detail="Production job not found")

        if job.status == "completed":
            raise HTTPException(
                status_code=400,
                detail="Cannot assign workers to a completed job",
            )

        # Validate worker
        worker = uow.users.get_by_id(assignment_data.worker_id)
        if not worker:
            raise HTTPException(
                status_code=404,
                detail=f"Worker {assignment_data.worker_id} not found",
            )

        # Check for existing assignment (active or previously removed)
        existing = (
            uow.session.query(JobWorkerAssignment)
            .filter(
                JobWorkerAssignment.job_id == job_id,
                JobWorkerAssignment.worker_id == assignment_data.worker_id,
            )
            .first()
        )

        if existing:
            if existing.removed_at is None:
                name = getattr(worker, "full_name", None) or worker.username
                raise HTTPException(
                    status_code=400,
                    detail=f"Worker {name} is already assigned to this job",
                )
            # Reactivate a previously removed assignment
            existing.removed_at = None
            existing.role = assignment_data.role
            existing.assigned_at = datetime.now(timezone.utc)
            uow.commit()
            uow.refresh(existing)
            logger.info(
                "Worker %s re-assigned to job %s",
                assignment_data.worker_id,
                job_id,
            )
            return existing

        # Create fresh assignment
        new_assignment = JobWorkerAssignment(
            job_id=job_id,
            worker_id=assignment_data.worker_id,
            role=assignment_data.role,
        )
        uow.session.add(new_assignment)
        uow.commit()

    uow.refresh(new_assignment)
    logger.info(
        "Worker %s assigned to job %s with role '%s'",
        assignment_data.worker_id,
        job_id,
        assignment_data.role,
    )
    return new_assignment


def remove_worker(
    uow: AbstractUnitOfWork,
    job_id: UUID,
    worker_id: UUID,
) -> JobWorkerAssignment:
    """
    Soft-remove a worker from a production job by setting removed_at.

    History is preserved so wage calculations for past days are unaffected.

    Args:
        uow (AbstractUnitOfWork): Unit of Work for database operations.
        job_id (UUID): Unique ID of the production job.
        worker_id (UUID): Unique ID of the worker to remove.

    Returns:
        JobWorkerAssignment: The updated (soft-removed) assignment record.

    Raises:
        HTTPException 404: If no active assignment exists.
    """
    with uow:
        assignment = (
            uow.session.query(JobWorkerAssignment)
            .filter(
                JobWorkerAssignment.job_id == job_id,
                JobWorkerAssignment.worker_id == worker_id,
                JobWorkerAssignment.removed_at.is_(None),
            )
            .first()
        )
        if not assignment:
            raise HTTPException(
                status_code=404,
                detail="Active assignment not found for this worker and job",
            )

        assignment.removed_at = datetime.now(timezone.utc)
        uow.commit()

    uow.refresh(assignment)
    logger.info("Worker %s removed from job %s", worker_id, job_id)
    return assignment
