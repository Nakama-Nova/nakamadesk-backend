"""
REST API routes for Production Job management.

Exposes endpoints for creating manufacturing jobs, listing/retrieving
them, and managing worker assignments. Follows the same project
conventions as orders.py, raw_materials.py et al.

RBAC design:
  - OWNER + MANAGER: full access
  - ACHARI: can view jobs assigned to them; may not create
  - WORKER: read-only on their assigned jobs (future scope)
"""

from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from app.db.deps import check_role, get_uow
from app.models.enums import UserRole
from app.models.user import User
from app.repositories.base import AbstractUnitOfWork
from app.schemas.production import (
    ProductionJobCreate,
    ProductionJobResponse,
    WorkerAssignmentCreate,
    WorkerAssignmentResponse,
)
from app.services import production_service

router = APIRouter(prefix="/production", tags=["Production"])


@router.post("/jobs", response_model=ProductionJobResponse, status_code=201)
def create_production_job(
    job_data: ProductionJobCreate,
    current_user: User = Depends(check_role([UserRole.OWNER, UserRole.MANAGER])),
    uow: AbstractUnitOfWork = Depends(get_uow),
):
    """
    Create a new manufacturing/production job.

    A job can be linked to a customer order (order_id) for made-to-order
    work, or standalone (order_id=None) for building stock speculatively.

    Either item_id (catalogue product) or custom_desc (bespoke piece)
    must be provided.

    RBAC: OWNER, MANAGER.

    Args:
        job_data (ProductionJobCreate): Validated production job payload.
        current_user (User): Authenticated user.
        uow (AbstractUnitOfWork): Unit of Work for database operations.

    Returns:
        ProductionJobResponse: The created job with worker assignments.
    """
    return production_service.create_job(uow, job_data, created_by=current_user.id)


@router.get("/jobs", response_model=List[ProductionJobResponse])
def list_production_jobs(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    status: Optional[str] = Query(
        None,
        description="Filter by status: pending|in_progress|completed|cancelled",
    ),
    order_id: Optional[UUID] = None,
    assigned_to: Optional[UUID] = None,
    current_user: User = Depends(
        check_role([UserRole.OWNER, UserRole.MANAGER, UserRole.ACHARI])
    ),
    uow: AbstractUnitOfWork = Depends(get_uow),
):
    """
    List production jobs with optional filtering and pagination.

    RBAC: OWNER, MANAGER, ACHARI.

    Args:
        limit (int): Maximum records to return.
        offset (int): Records to skip.
        status (Optional[str]): Filter by job status.
        order_id (Optional[UUID]): Filter by linked order.
        assigned_to (Optional[UUID]): Filter by supervisor.
        current_user (User): Authenticated user.
        uow (AbstractUnitOfWork): Unit of Work.

    Returns:
        List[ProductionJobResponse]: Matching jobs with assignments.
    """
    return production_service.list_jobs(
        uow,
        limit=limit,
        offset=offset,
        status=status,
        order_id=order_id,
        assigned_to=assigned_to,
    )


@router.get("/jobs/{job_id}", response_model=ProductionJobResponse)
def get_production_job(
    job_id: UUID,
    current_user: User = Depends(
        check_role([UserRole.OWNER, UserRole.MANAGER, UserRole.ACHARI])
    ),
    uow: AbstractUnitOfWork = Depends(get_uow),
):
    """
    Retrieve a specific production job by its unique ID.

    RBAC: OWNER, MANAGER, ACHARI.

    Args:
        job_id (UUID): Unique identifier of the production job.
        current_user (User): Authenticated user.
        uow (AbstractUnitOfWork): Unit of Work.

    Returns:
        ProductionJobResponse: Job details with worker assignments.

    Raises:
        HTTPException 404: If the job does not exist.
    """
    job = production_service.get_job(uow, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Production job not found")
    return job


@router.post(
    "/jobs/{job_id}/assign-worker",
    response_model=WorkerAssignmentResponse,
    status_code=201,
)
def assign_worker_to_job(
    job_id: UUID,
    assignment_data: WorkerAssignmentCreate,
    current_user: User = Depends(check_role([UserRole.OWNER, UserRole.MANAGER])),
    uow: AbstractUnitOfWork = Depends(get_uow),
):
    """
    Assign a worker to a production job with an optional craft role.

    Idempotent: re-assigning a previously removed worker reactivates
    their record rather than creating a duplicate.

    RBAC: OWNER, MANAGER.

    Args:
        job_id (UUID): Unique identifier of the production job.
        assignment_data (WorkerAssignmentCreate): Worker ID and craft role.
        current_user (User): Authenticated user.
        uow (AbstractUnitOfWork): Unit of Work.

    Returns:
        WorkerAssignmentResponse: The created or reactivated assignment.

    Raises:
        HTTPException 404: If job or worker not found.
        HTTPException 400: If worker is already actively assigned.
    """
    return production_service.assign_worker(uow, job_id, assignment_data)


@router.delete(
    "/jobs/{job_id}/workers/{worker_id}",
    response_model=WorkerAssignmentResponse,
)
def remove_worker_from_job(
    job_id: UUID,
    worker_id: UUID,
    current_user: User = Depends(check_role([UserRole.OWNER, UserRole.MANAGER])),
    uow: AbstractUnitOfWork = Depends(get_uow),
):
    """
    Soft-remove a worker from a production job.

    Sets removed_at on the assignment record; does not delete it.
    This preserves history for wage calculations.

    RBAC: OWNER, MANAGER.

    Args:
        job_id (UUID): Unique identifier of the production job.
        worker_id (UUID): Unique identifier of the worker to remove.
        current_user (User): Authenticated user.
        uow (AbstractUnitOfWork): Unit of Work.

    Returns:
        WorkerAssignmentResponse: The soft-removed assignment record.

    Raises:
        HTTPException 404: If no active assignment is found.
    """
    return production_service.remove_worker(uow, job_id, worker_id)
