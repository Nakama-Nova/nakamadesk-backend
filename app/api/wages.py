from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from app.db.deps import get_current_user, get_uow
from app.iam import service as iam
from app.iam.service import require
from app.models.user import User
from app.repositories.base import AbstractUnitOfWork
from app.schemas.wage import WagePaymentRequest, WageResponse
from app.services import workforce_service

router = APIRouter(prefix="/wages", tags=["Workforce"])


@router.get("/pending", response_model=List[WageResponse])
def list_pending_wages(
    user_id: Optional[UUID] = None,
    current_user: User = Depends(get_current_user),
    uow: AbstractUnitOfWork = Depends(get_uow),
):
    """
    Retrieve a list of pending wages for workers.

    Needs payroll:records:read_all to see all; others see only their own.

    Args:
        user_id (Optional[UUID]): Filter by a specific user (admin only).
        current_user (User): Authenticated user.
        uow (AbstractUnitOfWork): Unit of Work for database operations.

    Returns:
        List[WageResponse]: List of pending wage records.
    """
    # RBAC: Non-admin see only their own pending wages
    if not iam.allowed(uow.session, current_user, "payroll:records:read_all"):
        user_id = current_user.id

    return workforce_service.get_pending_wages(uow, user_id)


@router.post("/pay", response_model=List[WageResponse])
def pay_worker_wages(
    payment_data: WagePaymentRequest,
    current_user: User = Depends(require("payroll:payment:create")),
    uow: AbstractUnitOfWork = Depends(get_uow),
):
    """
    Record payment for a set of pending wages.

    Needs payroll:payment:create.

    Args:
        payment_data (WagePaymentRequest): List of attendance record IDs to pay.
        current_user (User): Authenticated user.
        uow (AbstractUnitOfWork): Unit of Work for database operations.

    Returns:
        List[WageResponse]: Updated records marked as paid.

    Raises:
        HTTPException: If no pending wages are found for the provided IDs.
    """
    wages = workforce_service.pay_wages(uow, payment_data)
    if not wages:
        raise HTTPException(
            status_code=400, detail="No pending wages found for the given IDs"
        )
    return wages
