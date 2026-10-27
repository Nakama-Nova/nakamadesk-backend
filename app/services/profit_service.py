"""
The real profit picture: what was billed, minus what actually went out.

sales      billed (cancelled sales left out), before any tax
purchases  confirmed raw-material purchases
wages      everything paid to staff: pay-outs (gross, before advance recovery) plus
           old-style /wages/pay payments that never went through a pay-out
expenses   rent, power, transport... (voided entries left out)
"""

from datetime import date
from decimal import Decimal
from typing import Dict, List, Optional

from sqlalchemy import Date, cast, func
from sqlalchemy.orm import Session

from app.models.daily_wage import DailyWage
from app.models.expense import Expense
from app.models.payroll import PayrollPayment, WorkEntry, WorkerAdvance
from app.models.purchase import Purchase
from app.models.raw_material import RawMaterial
from app.models.sale import Sale
from app.services.payroll_service import q

ZERO = Decimal("0.00")


def _in_range(query, column, start: Optional[date], end: Optional[date]):
    day = cast(column, Date)
    if start:
        query = query.filter(day >= start)
    if end:
        query = query.filter(day <= end)
    return query


def _by_month(db: Session, column, amount, *filters, start=None, end=None) -> Dict:
    month = func.to_char(column, "YYYY-MM")
    query = db.query(month, func.coalesce(func.sum(amount), 0)).filter(*filters)
    query = _in_range(query, column, start, end).group_by(month)
    return {m: q(total) for m, total in query.all()}


def expenses_by_category(
    db: Session, start: Optional[date], end: Optional[date]
) -> List[dict]:
    query = db.query(Expense.category, func.sum(Expense.amount)).filter(
        Expense.voided_at.is_(None)
    )
    query = _in_range(query, Expense.spent_on, start, end)
    rows = query.group_by(Expense.category).order_by(func.sum(Expense.amount).desc())
    return [{"category": c, "total": q(t)} for c, t in rows.all()]


def profit_report(db: Session, start: Optional[date], end: Optional[date]) -> dict:
    sales = _by_month(
        db,
        Sale.created_at,
        func.coalesce(Sale.total_amount, 0) - func.coalesce(Sale.tax_total, 0),
        func.coalesce(Sale.order_status, "completed") != "cancelled",
        start=start,
        end=end,
    )
    purchases = _by_month(
        db,
        Purchase.purchase_date,
        Purchase.total_amount,
        Purchase.status == "confirmed",
        start=start,
        end=end,
    )
    payouts = _by_month(
        db,
        PayrollPayment.paid_on,
        PayrollPayment.gross_daily + PayrollPayment.gross_extras,
        start=start,
        end=end,
    )
    legacy = _by_month(
        db,
        DailyWage.paid_at,
        DailyWage.amount,
        DailyWage.payment_status == "paid",
        DailyWage.payment_id.is_(None),
        start=start,
        end=end,
    )
    expenses = _by_month(
        db,
        Expense.spent_on,
        Expense.amount,
        Expense.voided_at.is_(None),
        start=start,
        end=end,
    )

    wages: Dict[str, Decimal] = {}
    for part in (payouts, legacy):
        for month, total in part.items():
            wages[month] = wages.get(month, ZERO) + total

    months = []
    for month in sorted({*sales, *purchases, *wages, *expenses}):
        s, p = sales.get(month, ZERO), purchases.get(month, ZERO)
        w, e = wages.get(month, ZERO), expenses.get(month, ZERO)
        months.append(
            {
                "month": month,
                "sales": s,
                "purchases": p,
                "wages": w,
                "expenses": e,
                "profit": s - p - w - e,
            }
        )

    total = {k: sum((m[k] for m in months), ZERO) for k in ("sales", "purchases")}
    total["wages"] = sum((m["wages"] for m in months), ZERO)
    total["expenses"] = sum((m["expenses"] for m in months), ZERO)
    profit = total["sales"] - total["purchases"] - total["wages"] - total["expenses"]
    margin = (
        (profit / total["sales"] * 100).quantize(Decimal("0.01"))
        if total["sales"] > 0
        else ZERO
    )

    unpaid_daily = (
        db.query(func.coalesce(func.sum(DailyWage.amount), 0))
        .filter(DailyWage.payment_status == "pending")
        .scalar()
    )
    unpaid_extras = (
        db.query(func.coalesce(func.sum(WorkEntry.amount), 0))
        .filter(WorkEntry.status == "pending")
        .scalar()
    )
    advances = db.query(func.coalesce(func.sum(WorkerAdvance.amount), 0)).scalar()
    stock_value = db.query(
        func.coalesce(func.sum(RawMaterial.stock * RawMaterial.current_price), 0)
    ).scalar()

    return {
        "start_date": start,
        "end_date": end,
        **total,
        "profit": profit,
        "margin_pct": margin,
        "expenses_by_category": expenses_by_category(db, start, end),
        "months": months,
        "unpaid_wages": q(unpaid_daily) + q(unpaid_extras),
        "advances_outstanding": q(advances),
        "raw_material_stock_value": q(stock_value),
    }
