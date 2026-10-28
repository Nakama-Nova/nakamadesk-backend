from datetime import date, datetime, timezone
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db.deps import RESTORATION_ROLES, check_role, get_db
from app.models.enums import UserRole
from app.models.expense import Expense
from app.models.restoration import RestorationProject
from app.models.user import User
from app.schemas.money import (
    ExpenseCreate,
    ExpenseResponse,
    ExpenseSummary,
    ExpenseUpdate,
    ExpenseVoid,
)
from app.services import profit_service

router = APIRouter(prefix="/expenses", tags=["Expenses"])

BOOKKEEPERS = [UserRole.OWNER, UserRole.MANAGER]


def _check_restoration(db: Session, user: User, restoration_id) -> None:
    """Linking an expense to a restoration is for the owner (who may see restorations)."""
    if restoration_id is None:
        return
    if user.role not in RESTORATION_ROLES:
        raise HTTPException(
            status_code=403,
            detail="Only the owner can link an expense to a restoration",
        )
    if not db.get(RestorationProject, restoration_id):
        raise HTTPException(status_code=404, detail="Restoration not found")


def _get(db: Session, expense_id: UUID) -> Expense:
    expense = db.get(Expense, expense_id)
    if not expense:
        raise HTTPException(status_code=404, detail="Expense not found")
    return expense


@router.post("/", response_model=ExpenseResponse, status_code=201)
def add_expense(
    data: ExpenseCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(check_role(BOOKKEEPERS)),
):
    """Record money spent (rent, power, transport, tools...). OWNER, MANAGER."""
    _check_restoration(db, current_user, data.restoration_id)
    fields = data.model_dump()
    fields["category"] = data.category.strip().title()
    expense = Expense(**fields, created_by=current_user.id)
    db.add(expense)
    db.commit()
    db.refresh(expense)
    return expense


@router.get("/", response_model=List[ExpenseResponse])
def list_expenses(
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    category: Optional[str] = Query(None),
    include_voided: bool = Query(False),
    restoration_id: Optional[UUID] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(check_role(BOOKKEEPERS)),
):
    query = db.query(Expense)
    if not include_voided:
        query = query.filter(Expense.voided_at.is_(None))
    if start_date:
        query = query.filter(Expense.spent_on >= start_date)
    if end_date:
        query = query.filter(Expense.spent_on <= end_date)
    if category:
        query = query.filter(func.lower(Expense.category) == category.lower())
    if restoration_id:
        query = query.filter(Expense.restoration_id == restoration_id)
    return query.order_by(Expense.spent_on.desc(), Expense.created_at.desc()).all()


@router.get("/summary", response_model=ExpenseSummary)
def expense_summary(
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(check_role(BOOKKEEPERS)),
):
    rows = profit_service.expenses_by_category(db, start_date, end_date)
    return {"total": sum((r["total"] for r in rows), 0), "by_category": rows}


@router.patch("/{expense_id}", response_model=ExpenseResponse)
def edit_expense(
    expense_id: UUID,
    data: ExpenseUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(check_role(BOOKKEEPERS)),
):
    expense = _get(db, expense_id)
    if expense.voided_at:
        raise HTTPException(status_code=409, detail="A voided expense cannot be edited")
    changes = data.model_dump(exclude_unset=True)
    if (
        "restoration_id" in changes
        and changes["restoration_id"] != expense.restoration_id
    ):
        # moving an expense onto, off or between projects changes what a piece has cost
        if (
            changes["restoration_id"] is None
            and current_user.role not in RESTORATION_ROLES
        ):
            raise HTTPException(
                status_code=403,
                detail="Only the owner can take an expense off a restoration",
            )
        _check_restoration(db, current_user, changes["restoration_id"])
    for key, value in changes.items():
        if key in ("category", "spent_on", "amount", "method") and value is None:
            continue  # these can never be blank
        setattr(expense, key, value.strip().title() if key == "category" else value)
    db.commit()
    db.refresh(expense)
    return expense


@router.post("/{expense_id}/void", response_model=ExpenseResponse)
def void_expense(
    expense_id: UUID,
    body: ExpenseVoid,
    db: Session = Depends(get_db),
    current_user: User = Depends(check_role([UserRole.OWNER])),
):
    """Cancel a wrong entry, keeping it on record with the reason. OWNER only."""
    expense = _get(db, expense_id)
    if expense.voided_at:
        raise HTTPException(status_code=409, detail="Already voided")
    expense.voided_at = datetime.now(timezone.utc)
    expense.voided_by = current_user.id
    expense.void_reason = body.reason
    db.commit()
    db.refresh(expense)
    return expense
