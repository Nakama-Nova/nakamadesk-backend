"""
Does the ledger agree with the reports the owner already trusts? This is the check that
runs through the shadow month (docs/FINANCE_LEDGER_DESIGN.md, section 8).

For each month in the window:
  * Sales: the profit report's sales (billed, before tax, cancelled left out) against what
    the ledger holds in the Sales account.
  * Customer money: all the order payments recorded, against what the ledger took in
    from them.
A month agrees only when both differences are zero. The run also says how much is still
to post or reverse (a dry run of the projector) and lists the records it could not post
cleanly.
"""

from datetime import date
from decimal import Decimal
from typing import Dict, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.finance import projector
from app.finance.models import FinanceAccount, FinanceEntry, FinanceLine
from app.finance.schemas import Reconciliation, ReconciliationMonth, ReconProblem
from app.models.order_payment import OrderPayment
from app.services import profit_service

ZERO = Decimal("0.00")
CENT = Decimal("0.01")


def _month(column):
    return func.to_char(column, "YYYY-MM")


def _ledger_sales(session: Session, date_from, date_to) -> Dict[str, Decimal]:
    """Credits minus debits on the Sales account, by month of the entry."""
    q = (
        session.query(
            _month(FinanceEntry.entry_date),
            func.coalesce(func.sum(FinanceLine.credit - FinanceLine.debit), 0),
        )
        .join(FinanceEntry, FinanceEntry.id == FinanceLine.entry_id)
        .join(FinanceAccount, FinanceAccount.id == FinanceLine.account_id)
        .filter(FinanceAccount.code == projector.SALES)
    )
    if date_from:
        q = q.filter(FinanceEntry.entry_date >= date_from)
    if date_to:
        q = q.filter(FinanceEntry.entry_date <= date_to)
    return {
        m: Decimal(t).quantize(CENT)
        for m, t in q.group_by(_month(FinanceEntry.entry_date))
    }


def _source_payments(session: Session, date_from, date_to) -> Dict[str, Decimal]:
    q = session.query(
        _month(OrderPayment.paid_on), func.coalesce(func.sum(OrderPayment.amount), 0)
    )
    if date_from:
        q = q.filter(OrderPayment.paid_on >= date_from)
    if date_to:
        q = q.filter(OrderPayment.paid_on <= date_to)
    return {
        m: Decimal(t).quantize(CENT)
        for m, t in q.group_by(_month(OrderPayment.paid_on))
    }


def _ledger_payments(session: Session, date_from, date_to) -> Dict[str, Decimal]:
    """Money taken in on order payments: debits minus credits on cash, bank and clearing."""
    q = (
        session.query(
            _month(FinanceEntry.entry_date),
            func.coalesce(func.sum(FinanceLine.debit - FinanceLine.credit), 0),
        )
        .join(FinanceEntry, FinanceEntry.id == FinanceLine.entry_id)
        .join(FinanceAccount, FinanceAccount.id == FinanceLine.account_id)
        .filter(
            FinanceEntry.source_type == "order_payment",
            FinanceAccount.code.in_(
                [projector.CASH, projector.BANK, projector.CLEARING]
            ),
        )
    )
    if date_from:
        q = q.filter(FinanceEntry.entry_date >= date_from)
    if date_to:
        q = q.filter(FinanceEntry.entry_date <= date_to)
    return {
        m: Decimal(t).quantize(CENT)
        for m, t in q.group_by(_month(FinanceEntry.entry_date))
    }


def _ledger_costs(
    session: Session, date_from, date_to, source_types, *account_filters
) -> Dict[str, Decimal]:
    """Debits minus credits on cost accounts, by month, for entries from these records."""
    q = (
        session.query(
            _month(FinanceEntry.entry_date),
            func.coalesce(func.sum(FinanceLine.debit - FinanceLine.credit), 0),
        )
        .join(FinanceEntry, FinanceEntry.id == FinanceLine.entry_id)
        .join(FinanceAccount, FinanceAccount.id == FinanceLine.account_id)
        .filter(FinanceEntry.source_type.in_(source_types), *account_filters)
    )
    if date_from:
        q = q.filter(FinanceEntry.entry_date >= date_from)
    if date_to:
        q = q.filter(FinanceEntry.entry_date <= date_to)
    return {
        m: Decimal(t).quantize(CENT)
        for m, t in q.group_by(_month(FinanceEntry.entry_date))
    }


def reconcile(
    session: Session, date_from: Optional[date], date_to: Optional[date]
) -> Reconciliation:
    report_months = {
        m["month"]: m
        for m in profit_service.profit_report(session, date_from, date_to)["months"]
    }

    def reported(key):
        return {m: Decimal(v[key]) for m, v in report_months.items()}

    ledger_sales = _ledger_sales(session, date_from, date_to)
    source_pay = _source_payments(session, date_from, date_to)
    ledger_pay = _ledger_payments(session, date_from, date_to)
    # (name, what the profit report says, what the ledger holds)
    costs = {
        "purchases": (
            reported("purchases"),
            _ledger_costs(
                session,
                date_from,
                date_to,
                ["purchase"],
                FinanceAccount.code == projector.PURCHASES,
            ),
        ),
        "wages": (
            reported("wages"),
            _ledger_costs(
                session,
                date_from,
                date_to,
                ["payroll_payment", "daily_wage"],
                FinanceAccount.code == projector.WAGES,
            ),
        ),
        "expenses": (
            reported("expenses"),
            _ledger_costs(
                session,
                date_from,
                date_to,
                ["expense"],
                FinanceAccount.type == "expense",
            ),
        ),
    }
    report_sales = reported("sales")

    keys = {*report_sales, *ledger_sales, *source_pay, *ledger_pay}
    for rep, led in costs.values():
        keys |= {*rep, *led}
    months = []
    for month in sorted(keys):
        r, ls = report_sales.get(month, ZERO), ledger_sales.get(month, ZERO)
        sp, lp = source_pay.get(month, ZERO), ledger_pay.get(month, ZERO)
        extra = {}
        for name, (rep, led) in costs.items():
            a, b = rep.get(month, ZERO), led.get(month, ZERO)
            extra[f"report_{name}"] = a
            extra[f"ledger_{name}"] = b
            extra[f"{name}_difference"] = (b - a).quantize(CENT)
        months.append(
            ReconciliationMonth(
                month=month,
                report_sales=r,
                ledger_sales=ls,
                sales_difference=(ls - r).quantize(CENT),
                order_payments=sp,
                ledger_order_payments=lp,
                payments_difference=(lp - sp).quantize(CENT),
                **extra,
            )
        )
    pending = projector.project(
        session, date_from=date_from, date_to=date_to, dry_run=True
    )
    agree = all(
        d == 0
        for m in months
        for d in (
            m.sales_difference,
            m.payments_difference,
            m.purchases_difference,
            m.wages_difference,
            m.expenses_difference,
        )
    )
    return Reconciliation(
        date_from=date_from,
        date_to=date_to,
        months=months,
        to_post=pending.posted,
        to_reverse=pending.reversed,
        problems=[
            ReconProblem(
                source_type=p.source_type,
                source_id=p.source_id,
                code=p.code,
                detail=p.detail,
            )
            for p in pending.problems
        ],
        agrees=agree and pending.posted == 0 and pending.reversed == 0,
    )
