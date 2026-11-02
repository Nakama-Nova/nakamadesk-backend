import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    DDL,
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    event,
    text,
)
from sqlalchemy.dialects.postgresql import UUID

from app.db.base import Base
from app.finance import ddl


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class FinanceAccount(Base):
    """One account in the chart of accounts. Accounts are switched off, never deleted."""

    __tablename__ = "finance_accounts"
    __table_args__ = (
        CheckConstraint(
            "type IN ('asset', 'liability', 'equity', 'income', 'expense')",
            name="ck_finance_accounts_type",
        ),
        CheckConstraint(
            "normal_side IN ('debit', 'credit')", name="ck_finance_accounts_side"
        ),
    )

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    code = Column(String, unique=True, nullable=False)  # "1000"
    name = Column(String, nullable=False)
    type = Column(String, nullable=False)
    # Which side makes the balance grow. Usually follows the type; a contra account
    # (depreciation so far) is an asset whose normal side is credit.
    normal_side = Column(String, nullable=False)
    is_active = Column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    created_at = Column(DateTime, nullable=False, default=_now)


class FinanceEntry(Base):
    """
    One journal entry: a dated set of lines whose debits equal its credits. Never edited
    or deleted (the database refuses). `kind`: manual (typed in by the owner), posting
    (made from a business record, see source_*), or reversal of an earlier entry.
    """

    __tablename__ = "finance_entries"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('manual', 'posting', 'reversal')", name="ck_finance_entries_kind"
        ),
        CheckConstraint(
            "(kind = 'reversal') = (reverses_id IS NOT NULL)",
            name="ck_finance_entries_reversal",
        ),
        CheckConstraint(
            "kind <> 'posting' OR source_type IS NOT NULL",
            name="ck_finance_entries_source",
        ),
        # an entry can be reversed once
        UniqueConstraint("reverses_id", name="uq_finance_entries_reverses"),
        # a business record is posted once per version of that record
        Index(
            "uq_finance_entries_source",
            "source_type",
            "source_id",
            "source_fingerprint",
            unique=True,
            postgresql_where=text("kind = 'posting'"),
        ),
        Index("ix_finance_entries_date", "entry_date"),
    )

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    entry_date = Column(Date, nullable=False)
    memo = Column(Text, nullable=False)
    kind = Column(String, nullable=False)
    source_type = Column(String, nullable=True)
    source_id = Column(String, nullable=True)
    source_fingerprint = Column(String, nullable=True)
    reverses_id = Column(
        UUID(as_uuid=True), ForeignKey("finance_entries.id"), nullable=True
    )
    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, nullable=False, default=_now)


class FinanceLine(Base):
    """One side of an entry: a debit or a credit to one account, never both."""

    __tablename__ = "finance_lines"
    __table_args__ = (
        CheckConstraint(
            "(debit > 0 AND credit = 0) OR (credit > 0 AND debit = 0)",
            name="ck_finance_lines_one_side",
        ),
        Index("ix_finance_lines_account", "account_id"),
        Index("ix_finance_lines_entry", "entry_id"),
    )

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    entry_id = Column(
        UUID(as_uuid=True), ForeignKey("finance_entries.id"), nullable=False
    )
    account_id = Column(
        UUID(as_uuid=True), ForeignKey("finance_accounts.id"), nullable=False
    )
    debit = Column(Numeric(14, 2), nullable=False, default=0, server_default="0")
    credit = Column(Numeric(14, 2), nullable=False, default=0, server_default="0")
    # where it happened (the Shop, a plant unit), so profit can be cut by place later
    location_id = Column(
        UUID(as_uuid=True), ForeignKey("plant_units.id"), nullable=True
    )
    party_type = Column(String, nullable=True)  # customer, supplier, staff
    party_id = Column(String, nullable=True)
    note = Column(String, nullable=True)


class FinanceSettings(Base):
    """Exactly one row (id 1). closed_through: nothing may be posted on or before it."""

    __tablename__ = "finance_settings"
    __table_args__ = (CheckConstraint("id = 1", name="ck_finance_settings_one_row"),)

    id = Column(Integer, primary_key=True, default=1)
    closed_through = Column(Date, nullable=True)
    updated_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    updated_at = Column(DateTime, nullable=False, default=_now, onupdate=_now)


# Create the rules together with the tables, wherever the tables are created from the
# models (tests, fresh development databases). The migration runs the same statements.
for _sql in ddl.INSTALL:
    event.listen(FinanceLine.__table__, "after_create", DDL(_sql))
event.listen(FinanceSettings.__table__, "after_create", DDL(ddl.SETTINGS_ROW))
