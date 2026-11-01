from datetime import date
from decimal import Decimal
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field

Category = Literal["machines", "raw_materials"]
Period = Literal["month", "financial_year"]


class BudgetIn(BaseModel):
    category: Category
    period: Period = "month"
    # Any day inside the period; it is moved to the first day of that month, or to
    # 1 April of that financial year.
    starts_on: date
    amount: Decimal = Field(ge=0, max_digits=12, decimal_places=2)
    notes: Optional[str] = Field(default=None, max_length=300)


class BudgetStatus(BaseModel):
    id: Optional[UUID] = None  # empty when no budget is set for this period
    category: str
    period: str
    period_start: date
    period_end: date
    amount: Optional[Decimal] = None
    spent: Decimal
    remaining: Optional[Decimal] = None  # negative when over
    percent_used: Optional[Decimal] = None
    # no_budget, ok, warning (80% or more used), over (all used)
    state: str
    # worth today, whatever the period: machines after depreciation; raw materials at
    # stock on hand x current price
    current_value: Decimal
    notes: Optional[str] = None
