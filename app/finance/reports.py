"""
The two statements read straight off the ledger (docs/FINANCE_LEDGER_DESIGN.md, section 6).

  Profit and loss  income minus expenses over a period.
  Balance sheet    what the business has (assets), owes (liabilities) and is worth
                   (equity) on a day. Profit so far is shown as its own line because
                   the books are never "closed" into equity; so assets always equal
                   liabilities plus equity plus profit so far.

Until the shadow month is signed off these are previews: the existing reports stay the
official numbers.
"""

from datetime import date
from decimal import Decimal
from typing import Dict, List, Optional, Tuple

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.finance.models import FinanceAccount, FinanceEntry, FinanceLine
from app.finance.schemas import BalanceSheet, ProfitAndLoss, ReportRow

ZERO = Decimal("0.00")


def _today() -> date:
    return date.today()


def _net_by_account(
    session: Session, date_from: Optional[date], date_to: Optional[date]
) -> Dict[object, Decimal]:
    """Debits minus credits per account for entries dated within the window."""
    q = (
        session.query(
            FinanceLine.account_id,
            func.coalesce(func.sum(FinanceLine.debit - FinanceLine.credit), 0),
        )
        .join(FinanceEntry, FinanceEntry.id == FinanceLine.entry_id)
        .group_by(FinanceLine.account_id)
    )
    if date_from:
        q = q.filter(FinanceEntry.entry_date >= date_from)
    if date_to:
        q = q.filter(FinanceEntry.entry_date <= date_to)
    return {account_id: Decimal(total) for account_id, total in q}


def _rows(
    accounts: List[FinanceAccount], net: Dict[object, Decimal], debit_side: bool
) -> Tuple[List[ReportRow], Decimal]:
    """The accounts that moved, with amounts counted in the section's own direction
    (so a contra account, like depreciation so far, comes out negative)."""
    rows, total = [], ZERO
    for account in accounts:
        amount = net.get(account.id, ZERO) * (1 if debit_side else -1)
        if amount == 0:
            continue
        rows.append(ReportRow(code=account.code, name=account.name, amount=amount))
        total += amount
    return rows, total


def _accounts(session: Session, kind: str) -> List[FinanceAccount]:
    return (
        session.query(FinanceAccount)
        .filter(FinanceAccount.type == kind)
        .order_by(FinanceAccount.code)
        .all()
    )


def profit_and_loss(
    session: Session, date_from: Optional[date], date_to: Optional[date]
) -> ProfitAndLoss:
    net = _net_by_account(session, date_from, date_to)
    income, total_income = _rows(_accounts(session, "income"), net, debit_side=False)
    expenses, total_expenses = _rows(
        _accounts(session, "expense"), net, debit_side=True
    )
    return ProfitAndLoss(
        date_from=date_from,
        date_to=date_to,
        income=income,
        expenses=expenses,
        total_income=total_income,
        total_expenses=total_expenses,
        net_profit=total_income - total_expenses,
    )


def balance_sheet(session: Session, as_of: Optional[date]) -> BalanceSheet:
    as_of = as_of or _today()
    net = _net_by_account(session, None, as_of)
    assets, total_assets = _rows(_accounts(session, "asset"), net, debit_side=True)
    liabilities, total_liabilities = _rows(
        _accounts(session, "liability"), net, debit_side=False
    )
    equity, total_equity = _rows(_accounts(session, "equity"), net, debit_side=False)
    _, income = _rows(_accounts(session, "income"), net, debit_side=False)
    _, expenses = _rows(_accounts(session, "expense"), net, debit_side=True)
    profit = income - expenses
    return BalanceSheet(
        as_of=as_of,
        assets=assets,
        liabilities=liabilities,
        equity=equity,
        profit_so_far=profit,
        total_assets=total_assets,
        total_liabilities=total_liabilities,
        total_equity=total_equity + profit,
        balanced=total_assets == total_liabilities + total_equity + profit,
    )
