"""finance ledger core: accounts, entries, lines, closing date, and the database rules

Additive only: new tables and functions, nothing existing changes. The rules (balanced
entries, no edits or deletes, closed months) are database triggers; the statements come
from app/finance/ddl.py so tests and this migration install the same thing.

Revision ID: f0a1b2c3d4e5
Revises: e9f0a1b2c3d4
"""

from datetime import datetime, timezone
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op
from app.finance import ddl

revision: str = "f0a1b2c3d4e5"
down_revision: Union[str, Sequence[str], None] = "e9f0a1b2c3d4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Frozen copy of the starting chart (the CA has not approved it yet; change it with a
# later migration or through the API, not by editing this one).
CHART = [
    ("1000", "Cash", "asset", "debit"),
    ("1010", "Bank and UPI", "asset", "debit"),
    ("1090", "Unmatched payments (clearing)", "asset", "debit"),
    ("1100", "Customers owe us", "asset", "debit"),
    ("1300", "Staff advances", "asset", "debit"),
    ("1400", "Machines (at cost)", "asset", "debit"),
    ("1410", "Machines: depreciation so far", "asset", "credit"),
    ("2000", "We owe suppliers", "liability", "credit"),
    ("2100", "Customer advances", "liability", "credit"),
    ("2200", "Wages owed", "liability", "credit"),
    ("2300", "GST payable", "liability", "credit"),
    ("3000", "Owner's capital and drawings", "equity", "credit"),
    ("4000", "Sales", "income", "credit"),
    ("5000", "Raw material bought", "expense", "debit"),
    ("5100", "Wages", "expense", "debit"),
    ("5200", "Repairs and maintenance", "expense", "debit"),
    ("5300", "Depreciation", "expense", "debit"),
    ("5400", "Rent, power, transport and other costs", "expense", "debit"),
    ("5900", "Other expenses", "expense", "debit"),
]

ID = dict(server_default=sa.text("gen_random_uuid()"), nullable=False)


def upgrade() -> None:
    op.create_table(
        "finance_accounts",
        sa.Column("id", sa.UUID(), **ID),
        sa.Column("code", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("type", sa.String(), nullable=False),
        sa.Column("normal_side", sa.String(), nullable=False),
        sa.Column(
            "is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "type IN ('asset', 'liability', 'equity', 'income', 'expense')",
            name="ck_finance_accounts_type",
        ),
        sa.CheckConstraint(
            "normal_side IN ('debit', 'credit')", name="ck_finance_accounts_side"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code"),
    )
    op.create_table(
        "finance_entries",
        sa.Column("id", sa.UUID(), **ID),
        sa.Column("entry_date", sa.Date(), nullable=False),
        sa.Column("memo", sa.Text(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("source_type", sa.String(), nullable=True),
        sa.Column("source_id", sa.String(), nullable=True),
        sa.Column("source_fingerprint", sa.String(), nullable=True),
        sa.Column("reverses_id", sa.UUID(), nullable=True),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "kind IN ('manual', 'posting', 'reversal')", name="ck_finance_entries_kind"
        ),
        sa.CheckConstraint(
            "(kind = 'reversal') = (reverses_id IS NOT NULL)",
            name="ck_finance_entries_reversal",
        ),
        sa.CheckConstraint(
            "kind <> 'posting' OR source_type IS NOT NULL",
            name="ck_finance_entries_source",
        ),
        sa.ForeignKeyConstraint(["reverses_id"], ["finance_entries.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("reverses_id", name="uq_finance_entries_reverses"),
    )
    op.create_index("ix_finance_entries_date", "finance_entries", ["entry_date"])
    op.create_index(
        "uq_finance_entries_source",
        "finance_entries",
        ["source_type", "source_id", "source_fingerprint"],
        unique=True,
        postgresql_where=sa.text("kind = 'posting'"),
    )
    op.create_table(
        "finance_lines",
        sa.Column("id", sa.UUID(), **ID),
        sa.Column("entry_id", sa.UUID(), nullable=False),
        sa.Column("account_id", sa.UUID(), nullable=False),
        sa.Column("debit", sa.Numeric(14, 2), server_default="0", nullable=False),
        sa.Column("credit", sa.Numeric(14, 2), server_default="0", nullable=False),
        sa.Column("location_id", sa.UUID(), nullable=True),
        sa.Column("party_type", sa.String(), nullable=True),
        sa.Column("party_id", sa.String(), nullable=True),
        sa.Column("note", sa.String(), nullable=True),
        sa.CheckConstraint(
            "(debit > 0 AND credit = 0) OR (credit > 0 AND debit = 0)",
            name="ck_finance_lines_one_side",
        ),
        sa.ForeignKeyConstraint(["entry_id"], ["finance_entries.id"]),
        sa.ForeignKeyConstraint(["account_id"], ["finance_accounts.id"]),
        sa.ForeignKeyConstraint(["location_id"], ["plant_units.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_finance_lines_account", "finance_lines", ["account_id"])
    op.create_index("ix_finance_lines_entry", "finance_lines", ["entry_id"])
    op.create_table(
        "finance_settings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("closed_through", sa.Date(), nullable=True),
        sa.Column("updated_by", sa.UUID(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("id = 1", name="ck_finance_settings_one_row"),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    for statement in ddl.INSTALL:
        op.execute(sa.text(statement.replace(":", "\\:")))
    op.execute(
        sa.text(
            "INSERT INTO finance_settings (id, updated_at) VALUES (1, now()) "
            "ON CONFLICT DO NOTHING"
        )
    )
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    accounts = sa.table(
        "finance_accounts",
        sa.column("code", sa.String),
        sa.column("name", sa.String),
        sa.column("type", sa.String),
        sa.column("normal_side", sa.String),
        sa.column("created_at", sa.DateTime),
    )
    op.bulk_insert(
        accounts,
        [
            {
                "code": c,
                "name": n,
                "type": t,
                "normal_side": s,
                "created_at": now,
            }
            for c, n, t, s in CHART
        ],
    )


def downgrade() -> None:
    # Dropping the triggers first is what lets the tables go; this undoes the whole
    # ledger, so only do it on a database that has never held real entries.
    for statement in ddl.UNINSTALL:
        op.execute(statement)
    op.drop_table("finance_settings")
    op.drop_index("ix_finance_lines_entry", table_name="finance_lines")
    op.drop_index("ix_finance_lines_account", table_name="finance_lines")
    op.drop_table("finance_lines")
    op.drop_index("uq_finance_entries_source", table_name="finance_entries")
    op.drop_index("ix_finance_entries_date", table_name="finance_entries")
    op.drop_table("finance_entries")
    op.drop_table("finance_accounts")
