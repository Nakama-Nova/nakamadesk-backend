from typing import List, Optional
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.deps import get_db, get_current_user, check_role
from app.models.user import User
from app.schemas.wage import WageResponse, WagePaymentRequest
from app.services import workforce_service

router = APIRouter(prefix="/wages", tags=["Workforce"])


@router.get("/pending", response_model=List[WageResponse])
def list_pending_wages(
    user_id: Optional[UUID] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    # RBAC: Non-admin see only their own pending wages
    if current_user.role not in ["owner", "manager"]:
        user_id = current_user.id

    return workforce_service.get_pending_wages(db, user_id)


@router.post("/pay", response_model=List[WageResponse])
def pay_worker_wages(
    payment_data: WagePaymentRequest,
    current_user: User = Depends(check_role(["owner", "manager"])),
    db: Session = Depends(get_db),
):
    wages = workforce_service.pay_wages(db, payment_data)
    if not wages:
        raise HTTPException(
            status_code=400, detail="No pending wages found for the given IDs"
        )
    return wages
