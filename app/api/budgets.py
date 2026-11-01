from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from app.db.deps import check_role, get_db
from app.models.enums import UserRole
from app.models.user import User
from app.schemas.budgets import BudgetIn, BudgetStatus
from app.services import budget_service as service

router = APIRouter(prefix="/budgets", tags=["Budgets"])

VIEW = [UserRole.OWNER, UserRole.MANAGER]
SET = [UserRole.OWNER]


@router.get("/current", response_model=List[BudgetStatus])
def current_budgets(_: User = Depends(check_role(VIEW)), db: Session = Depends(get_db)):
    """
    This month and this financial year (April to March) for Machines and for raw
    materials: the budget if one is set, what has been spent, what is left, how much is
    used, and what the machines or the stock are worth today.
    """
    return service.current(db)


@router.get("/warnings", response_model=List[BudgetStatus])
def budget_warnings(_: User = Depends(check_role(VIEW)), db: Session = Depends(get_db)):
    """Only the current budgets that are 80% used or over. Purchases are never blocked."""
    return service.warnings(db)


@router.get("/", response_model=List[BudgetStatus])
def list_budgets(
    category: Optional[str] = Query(None, pattern="^(machines|raw_materials)$"),
    period: Optional[str] = Query(None, pattern="^(month|financial_year)$"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    _: User = Depends(check_role(VIEW)),
    db: Session = Depends(get_db),
):
    """Every budget that has been set, newest period first, with its figures."""
    return service.list_budgets(db, category, period, limit, offset)


@router.put("/", response_model=BudgetStatus)
def set_budget(
    body: BudgetIn,
    current_user: User = Depends(check_role(SET)),
    db: Session = Depends(get_db),
):
    """Owner only. Sets the budget for the period holding `starts_on`, or changes it."""
    return service.set_budget(db, body, current_user.id)


@router.delete("/{budget_id}", status_code=204)
def delete_budget(
    budget_id: UUID,
    _: User = Depends(check_role(SET)),
    db: Session = Depends(get_db),
):
    """Owner only. Removes a budget (the spending records are untouched)."""
    service.delete_budget(db, budget_id)
    return Response(status_code=204)
