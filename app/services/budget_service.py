"""
Budgets for Machines and for raw materials. A budget stores only the amount; everything
else is worked out from the real records each time:

  spent
    machines      cost of machines bought in the period (by purchase date) plus the
                  cost of maintenance done in it (by the date it was done)
    raw_materials raw material bought: the line totals of CONFIRMED purchases dated in
                  the period. Wood that was collected free (reclaimed) has no purchase,
                  so it adds stock but spends nothing
  current value (as of today, whatever the period)
    machines      what the machines still in use are worth after depreciation
    raw_materials stock on hand x its current price

Going over a budget only warns. Nothing here blocks a purchase.
"""

from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import List, Optional
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.budget import Budget
from app.models.machine import Machine, MachineMaintenance
from app.models.purchase import Purchase
from app.models.purchase_item import PurchaseItem
from app.models.raw_material import RawMaterial
from app.schemas.budgets import BudgetIn, BudgetStatus
from app.services import machine_service

CENT = Decimal("0.01")
WARN_AT = Decimal("80")
CATEGORIES = ("machines", "raw_materials")
PERIODS = ("month", "financial_year")


def period_bounds(period: str, on: date) -> tuple:
    """(first day, first day after) of the month or Indian financial year holding `on`."""
    if period == "month":
        start = on.replace(day=1)
        end = (start + timedelta(days=32)).replace(day=1)
        return start, end
    year = on.year if on.month >= 4 else on.year - 1
    return date(year, 4, 1), date(year + 1, 4, 1)


def _sum(q) -> Decimal:
    return Decimal(q.scalar() or 0)


def spent(session: Session, category: str, start: date, end: date) -> Decimal:
    if category == "machines":
        bought = _sum(
            session.query(func.coalesce(func.sum(Machine.purchase_cost), 0)).filter(
                Machine.purchase_date >= start, Machine.purchase_date < end
            )
        )
        upkeep = _sum(
            session.query(func.coalesce(func.sum(MachineMaintenance.cost), 0)).filter(
                MachineMaintenance.performed_on >= start,
                MachineMaintenance.performed_on < end,
            )
        )
        return (bought + upkeep).quantize(CENT)
    return _sum(
        session.query(func.coalesce(func.sum(PurchaseItem.line_total), 0))
        .join(Purchase, Purchase.id == PurchaseItem.purchase_id)
        .filter(
            Purchase.status == "confirmed",
            PurchaseItem.raw_material_id.isnot(None),
            Purchase.purchase_date >= start,
            Purchase.purchase_date < end,
        )
    ).quantize(CENT)


def current_value(session: Session, category: str) -> Decimal:
    if category == "machines":
        total = Decimal("0")
        for machine in session.query(Machine).filter(Machine.status != "retired"):
            _, worth = machine_service.depreciation(machine)
            total += worth or Decimal("0")
        return total.quantize(CENT)
    return _sum(
        session.query(
            func.coalesce(func.sum(RawMaterial.stock * RawMaterial.current_price), 0)
        )
    ).quantize(CENT)


def _status(
    session: Session,
    category: str,
    period: str,
    start: date,
    end: date,
    budget: Optional[Budget],
) -> BudgetStatus:
    used = spent(session, category, start, end)
    amount = Decimal(budget.amount) if budget else None
    percent = remaining = None
    if amount is None:
        state = "no_budget"
    else:
        remaining = (amount - used).quantize(CENT)
        if amount > 0:
            percent = (used / amount * 100).quantize(CENT, rounding=ROUND_HALF_UP)
            state = (
                "over" if used >= amount else "warning" if percent >= WARN_AT else "ok"
            )
        else:
            state = "over" if used > 0 else "ok"
    return BudgetStatus(
        id=budget.id if budget else None,
        category=category,
        period=period,
        period_start=start,
        period_end=end - timedelta(days=1),
        amount=amount,
        spent=used,
        remaining=remaining,
        percent_used=percent,
        state=state,
        current_value=current_value(session, category),
        notes=budget.notes if budget else None,
    )


def _of(session: Session, budget: Budget) -> BudgetStatus:
    start, end = period_bounds(budget.period, budget.period_start)
    return _status(session, budget.category, budget.period, start, end, budget)


def set_budget(session: Session, data: BudgetIn, user_id: UUID) -> BudgetStatus:
    """Set (or change) the budget for the period holding `starts_on`."""
    start, _ = period_bounds(data.period, data.starts_on)
    budget = (
        session.query(Budget)
        .filter(
            Budget.category == data.category,
            Budget.period == data.period,
            Budget.period_start == start,
        )
        .first()
    )
    if budget is None:
        budget = Budget(category=data.category, period=data.period, period_start=start)
        session.add(budget)
    budget.amount = data.amount
    budget.notes = data.notes
    budget.set_by = user_id
    session.commit()
    session.refresh(budget)
    return _of(session, budget)


def delete_budget(session: Session, budget_id: UUID) -> None:
    budget = session.get(Budget, budget_id)
    if budget is None:
        raise HTTPException(status_code=404, detail="Budget not found")
    session.delete(budget)
    session.commit()


def list_budgets(
    session: Session,
    category: Optional[str],
    period: Optional[str],
    limit: int,
    offset: int,
) -> List[BudgetStatus]:
    q = session.query(Budget)
    if category:
        q = q.filter(Budget.category == category)
    if period:
        q = q.filter(Budget.period == period)
    rows = (
        q.order_by(Budget.period_start.desc(), Budget.category)
        .limit(limit)
        .offset(offset)
        .all()
    )
    return [_of(session, b) for b in rows]


def current(session: Session, today: Optional[date] = None) -> List[BudgetStatus]:
    """Today's month and financial year for each category; amount empty if none is set."""
    today = today or date.today()
    out = []
    for category in CATEGORIES:
        for period in PERIODS:
            start, end = period_bounds(period, today)
            budget = (
                session.query(Budget)
                .filter(
                    Budget.category == category,
                    Budget.period == period,
                    Budget.period_start == start,
                )
                .first()
            )
            out.append(_status(session, category, period, start, end, budget))
    return out


def warnings(session: Session, today: Optional[date] = None) -> List[BudgetStatus]:
    """Only the budgets at 80% or more, or over."""
    return [s for s in current(session, today) if s.state in ("warning", "over")]
