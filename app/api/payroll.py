from datetime import date
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db.deps import check_role, get_current_user, get_db
from app.models.enums import UserRole
from app.models.user import User
from app.schemas.money import (
    AdvanceCreate,
    AdvanceLedger,
    AdvanceResponse,
    MyPayroll,
    OwedSummary,
    PaymentResponse,
    PayRequest,
    WorkEntryCreate,
    WorkEntryResponse,
)
from app.services import payroll_service

router = APIRouter(prefix="/payroll", tags=["Payroll"])

PAYMASTERS = [UserRole.OWNER, UserRole.MANAGER]


@router.get("/me", response_model=MyPayroll)
def my_payroll(
    db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
):
    """What I am owed, my advance balance and my latest pay-outs. Any staff."""
    return payroll_service.my_payroll(db, db.get(User, current_user.id))


@router.get("/summary", response_model=List[OwedSummary])
def everyone_owed(
    db: Session = Depends(get_db),
    current_user: User = Depends(check_role(PAYMASTERS)),
):
    """Every active person: what they are owed and what they owe back."""
    return payroll_service.everyone_owed(db)


@router.post("/work-entries", response_model=WorkEntryResponse, status_code=201)
def add_work_entry(
    data: WorkEntryCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(check_role(PAYMASTERS)),
):
    """Pay by the piece, overtime hours or a bonus, on top of the daily wage."""
    return payroll_service.create_work_entry(db, current_user, data)


@router.get("/work-entries", response_model=List[WorkEntryResponse])
def list_work_entries(
    user_id: Optional[UUID] = Query(None),
    status: Optional[str] = Query(None, pattern="^(pending|paid)$"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if current_user.role not in PAYMASTERS:
        user_id = current_user.id  # everyone else sees only their own
    return payroll_service.list_work_entries(db, user_id, status)


@router.delete("/work-entries/{entry_id}", status_code=204)
def delete_work_entry(
    entry_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(check_role(PAYMASTERS)),
):
    """Remove a mistaken entry. Only while it is still unpaid."""
    payroll_service.delete_work_entry(db, entry_id)


@router.post("/advances", response_model=AdvanceResponse, status_code=201)
def add_advance(
    data: AdvanceCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(check_role(PAYMASTERS)),
):
    """Lend money to staff (kind=advance) or record a cash repayment (kind=recovery)."""
    return payroll_service.add_advance(db, current_user, data)


@router.get("/advances/{user_id}", response_model=AdvanceLedger)
def advance_ledger(
    user_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if current_user.role not in PAYMASTERS and user_id != current_user.id:
        user_id = current_user.id
    return payroll_service.advance_ledger(db, user_id)


@router.post("/pay", response_model=PaymentResponse, status_code=201)
def pay(
    data: PayRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(check_role(PAYMASTERS)),
):
    """
    Pay one person everything earned up to a date, optionally taking back part of
    an advance. This is what marks wages paid and counts them in the profit report.
    """
    return payroll_service.pay(db, current_user, data)


@router.get("/payments", response_model=List[PaymentResponse])
def list_payments(
    user_id: Optional[UUID] = Query(None),
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if current_user.role not in PAYMASTERS:
        user_id = current_user.id
    return payroll_service.list_payments(db, user_id, start_date, end_date)
