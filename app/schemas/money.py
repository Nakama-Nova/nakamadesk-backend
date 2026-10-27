from datetime import date, datetime
from decimal import Decimal
from typing import List, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

Money = Decimal  # always rounded to 2 places by the services
PayMethod = Literal["cash", "upi", "bank", "cheque", "other"]


class ExpenseCreate(BaseModel):
    spent_on: date
    category: str = Field(min_length=1, max_length=60)
    amount: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    payee: Optional[str] = Field(default=None, max_length=120)
    method: PayMethod = "cash"
    notes: Optional[str] = Field(default=None, max_length=500)


class ExpenseUpdate(BaseModel):
    spent_on: Optional[date] = None
    category: Optional[str] = Field(default=None, min_length=1, max_length=60)
    amount: Optional[Decimal] = Field(
        default=None, gt=0, max_digits=12, decimal_places=2
    )
    payee: Optional[str] = Field(default=None, max_length=120)
    method: Optional[PayMethod] = None
    notes: Optional[str] = Field(default=None, max_length=500)


class ExpenseVoid(BaseModel):
    reason: str = Field(min_length=3, max_length=200)


class ExpenseResponse(BaseModel):
    id: UUID
    spent_on: date
    category: str
    amount: Decimal
    payee: Optional[str] = None
    method: str
    notes: Optional[str] = None
    created_by: Optional[UUID] = None
    created_at: datetime
    voided_at: Optional[datetime] = None
    void_reason: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class CategoryTotal(BaseModel):
    category: str
    total: Decimal


class ExpenseSummary(BaseModel):
    total: Decimal
    by_category: List[CategoryTotal]


class WorkEntryCreate(BaseModel):
    user_id: UUID
    work_date: date
    kind: Literal["piece", "overtime", "bonus"]
    description: str = Field(min_length=1, max_length=200)
    # piece: number of pieces; overtime: hours; bonus: leave as 1
    quantity: Decimal = Field(default=Decimal("1"), gt=0, max_digits=10)
    # per piece / per hour; for a bonus this IS the amount. Overtime may leave it
    # out to use the person's overtime rate.
    rate: Optional[Decimal] = Field(default=None, ge=0, max_digits=10)
    item_id: Optional[UUID] = None


class WorkEntryResponse(BaseModel):
    id: UUID
    user_id: UUID
    work_date: date
    kind: str
    description: str
    quantity: Decimal
    rate: Decimal
    amount: Decimal
    item_id: Optional[UUID] = None
    status: str
    payment_id: Optional[UUID] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class AdvanceCreate(BaseModel):
    user_id: UUID
    given_on: date
    amount: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    # advance = lend; recovery = the person paid some back in cash
    kind: Literal["advance", "recovery"] = "advance"
    notes: Optional[str] = Field(default=None, max_length=300)


class AdvanceResponse(BaseModel):
    id: UUID
    user_id: UUID
    given_on: date
    amount: Decimal  # positive = lent, negative = taken back
    kind: str
    notes: Optional[str] = None
    payment_id: Optional[UUID] = None

    model_config = ConfigDict(from_attributes=True)


class AdvanceLedger(BaseModel):
    balance: Decimal
    entries: List[AdvanceResponse]


class PayRequest(BaseModel):
    user_id: UUID
    through: date  # pay everything earned up to and including this date
    paid_on: Optional[date] = None  # default: today
    deduction: Decimal = Field(default=Decimal("0"), ge=0, max_digits=12)
    method: PayMethod = "cash"
    reference: Optional[str] = Field(default=None, max_length=80)
    notes: Optional[str] = Field(default=None, max_length=300)


class PaymentResponse(BaseModel):
    id: UUID
    user_id: UUID
    paid_on: date
    through: date
    gross_daily: Decimal
    gross_extras: Decimal
    deductions: Decimal
    net_paid: Decimal
    method: str
    reference: Optional[str] = None
    notes: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class OwedSummary(BaseModel):
    """What one person is owed right now and what they owe back."""

    user_id: UUID
    full_name: Optional[str] = None
    username: str
    role: str
    pending_daily: Decimal
    pending_extras: Decimal
    owed_gross: Decimal
    advance_balance: Decimal


class MyPayroll(OwedSummary):
    pending_entries: List[WorkEntryResponse]
    recent_payments: List[PaymentResponse]


class MonthLine(BaseModel):
    month: str  # YYYY-MM
    sales: Decimal
    purchases: Decimal
    wages: Decimal
    expenses: Decimal
    profit: Decimal


class ProfitReport(BaseModel):
    """
    Cash-basis profit: what was billed minus what actually went out in the same
    dates. Buying timber counts when bought; the stock still on the shelf is shown
    separately so a big buy does not look like a loss for ever.
    """

    start_date: Optional[date] = None
    end_date: Optional[date] = None
    sales: Decimal
    purchases: Decimal
    wages: Decimal
    expenses: Decimal
    profit: Decimal
    margin_pct: Decimal
    expenses_by_category: List[CategoryTotal]
    months: List[MonthLine]
    unpaid_wages: Decimal  # earned, not yet paid (as of now)
    advances_outstanding: Decimal  # lent to staff, not yet taken back (as of now)
    raw_material_stock_value: Decimal  # as of now, at current prices
