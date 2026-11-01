import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID

from app.db.base import Base


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Budget(Base):
    """
    How much the owner is willing to spend on one thing over one period.

    category: `machines` (buying machines and keeping them running) or `raw_materials`
    (raw material bought). period: a calendar `month`, or a `financial_year` (April to
    March). `period_start` is the first day of that period. Only the amount is stored;
    what has been spent, what is left and what the stock is worth are worked out from
    the real records (budget_service), so a budget can never drift from the books.
    """

    __tablename__ = "budgets"
    __table_args__ = (
        UniqueConstraint(
            "category", "period", "period_start", name="uq_budgets_category_period"
        ),
        CheckConstraint(
            "category IN ('machines', 'raw_materials')", name="ck_budgets_category"
        ),
        CheckConstraint(
            "period IN ('month', 'financial_year')", name="ck_budgets_period"
        ),
        CheckConstraint("amount >= 0", name="ck_budgets_amount"),
    )

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    category = Column(String, nullable=False)
    period = Column(String, nullable=False)
    period_start = Column(Date, nullable=False)
    amount = Column(Numeric(12, 2), nullable=False)
    notes = Column(Text, nullable=True)
    set_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, nullable=False, default=_now)
    updated_at = Column(DateTime, nullable=False, default=_now, onupdate=_now)
