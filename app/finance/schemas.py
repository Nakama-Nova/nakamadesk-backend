from datetime import date, datetime
from decimal import Decimal
from typing import List, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

AccountType = Literal["asset", "liability", "equity", "income", "expense"]
Money = Field(default=None, ge=0, max_digits=14, decimal_places=2)


# ---------------------------------------------------------------------- accounts
class AccountIn(BaseModel):
    code: str = Field(pattern=r"^\d{4,6}$")
    name: str = Field(min_length=1, max_length=100)
    type: AccountType
    # Leave out and it follows the type. Say "credit" for an asset that works against
    # others (depreciation so far) or "debit" for the rare opposite.
    normal_side: Optional[Literal["debit", "credit"]] = None


class AccountUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    is_active: Optional[bool] = None


class AccountOut(BaseModel):
    id: UUID
    code: str
    name: str
    type: str
    normal_side: str
    is_active: bool
    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------------- journal
class LineIn(BaseModel):
    account: str = Field(pattern=r"^\d{4,6}$", description="Account code")
    debit: Optional[Decimal] = Money
    credit: Optional[Decimal] = Money
    location_id: Optional[UUID] = None
    note: Optional[str] = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def one_side(self):
        d, c = self.debit or Decimal("0"), self.credit or Decimal("0")
        if (d > 0) == (c > 0):
            raise ValueError(
                "each line needs a debit or a credit, not both and not neither"
            )
        return self


class JournalIn(BaseModel):
    entry_date: date
    memo: str = Field(min_length=3, max_length=500)
    lines: List[LineIn] = Field(min_length=2, max_length=50)


class ReverseIn(BaseModel):
    reason: str = Field(min_length=3, max_length=500)
    # Default: today. A reversal never lands inside a closed month.
    entry_date: Optional[date] = None


class LineOut(BaseModel):
    account: str
    account_name: str
    debit: Decimal
    credit: Decimal
    location_id: Optional[UUID] = None
    note: Optional[str] = None


class EntryOut(BaseModel):
    id: UUID
    entry_date: date
    memo: str
    kind: str
    source_type: Optional[str] = None
    source_id: Optional[str] = None
    reverses_id: Optional[UUID] = None
    reversed_by: Optional[UUID] = None
    created_by: Optional[UUID] = None
    created_at: datetime
    lines: List[LineOut]
    total: Decimal  # the amount moved (sum of the debits)


# ---------------------------------------------------------------------- reports
class TrialBalanceRow(BaseModel):
    code: str
    name: str
    type: str
    debit_total: Decimal
    credit_total: Decimal
    debit_balance: Decimal  # net, shown in the debit column when debits exceed credits
    credit_balance: Decimal
    balance: Decimal  # in the account's normal direction (negative = the unusual side)


class TrialBalance(BaseModel):
    as_of: date
    rows: List[TrialBalanceRow]
    debit_balance_total: Decimal
    credit_balance_total: Decimal
    balanced: bool


class LedgerLine(BaseModel):
    entry_id: UUID
    entry_date: date
    memo: str
    kind: str
    debit: Decimal
    credit: Decimal
    balance: Decimal  # running, in the account's normal direction


class Ledger(BaseModel):
    code: str
    name: str
    normal_side: str
    date_from: Optional[date] = None
    date_to: Optional[date] = None
    opening_balance: Decimal
    closing_balance: Decimal
    lines: List[LedgerLine]


class Closing(BaseModel):
    closed_through: Optional[date] = None
    updated_by: Optional[UUID] = None
    updated_at: Optional[datetime] = None


class ClosingIn(BaseModel):
    # None opens every month again. Moving it back needs the owner's reason, which is
    # kept in the audit trail along with who changed it.
    closed_through: Optional[date] = None
    reason: str = Field(min_length=3, max_length=300)


# ---------------------------------------------------------------- projector and checks
class SyncIn(BaseModel):
    date_from: Optional[date] = None
    date_to: Optional[date] = None


class ProblemOut(BaseModel):
    source_type: str
    source_id: str
    code: str
    detail: str


class SyncOut(BaseModel):
    posted: int  # new entries written (including re-posts of changed records)
    reversed: int  # old entries reversed (changed or no longer counted records)
    unchanged: int
    nothing_to_post: int
    problems: List[ProblemOut]


ReconProblem = ProblemOut


class ReconciliationMonth(BaseModel):
    month: str  # YYYY-MM
    report_sales: Decimal  # from the profit report
    ledger_sales: Decimal
    sales_difference: Decimal  # ledger minus report
    order_payments: Decimal  # recorded on orders
    ledger_order_payments: Decimal
    payments_difference: Decimal
    # purchases, wages and expenses: the profit report against the ledger
    report_purchases: Decimal = Decimal("0.00")
    ledger_purchases: Decimal = Decimal("0.00")
    purchases_difference: Decimal = Decimal("0.00")
    report_wages: Decimal = Decimal("0.00")
    ledger_wages: Decimal = Decimal("0.00")
    wages_difference: Decimal = Decimal("0.00")
    report_expenses: Decimal = Decimal("0.00")
    ledger_expenses: Decimal = Decimal("0.00")
    expenses_difference: Decimal = Decimal("0.00")


class Reconciliation(BaseModel):
    date_from: Optional[date] = None
    date_to: Optional[date] = None
    months: List[ReconciliationMonth]
    to_post: int  # records the ledger does not have yet
    to_reverse: int  # entries that no longer match their record
    problems: List[ReconProblem]
    # every month agrees to the paisa and nothing is waiting to be posted
    agrees: bool


class ReportRow(BaseModel):
    code: str
    name: str
    amount: Decimal  # counted in the section's own direction


class ProfitAndLoss(BaseModel):
    date_from: Optional[date] = None
    date_to: Optional[date] = None
    income: List[ReportRow]
    expenses: List[ReportRow]
    total_income: Decimal
    total_expenses: Decimal
    net_profit: Decimal  # income minus expenses


class BalanceSheet(BaseModel):
    as_of: date
    assets: List[ReportRow]
    liabilities: List[ReportRow]
    equity: List[ReportRow]
    profit_so_far: (
        Decimal  # all income minus all expenses up to the day, not yet in equity
    )
    total_assets: Decimal
    total_liabilities: Decimal
    total_equity: Decimal  # includes profit so far
    balanced: bool  # assets = liabilities + equity (always, if the books balance)
