"""Grant sales and purchases actions to the built-in roles

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-11-23 23:00:00.000000

Data only. Adds the actions the sales/orders/invoices/customers/purchases endpoints now
ask for to the built-in manager and sales policies, so a database that already ran the
IAM migration keeps the same access. Safe to run twice (it only adds what is missing).
The lists are frozen here on purpose: later changes to app/iam/defaults.py must not
rewrite what this migration did.
"""

import json
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "e5f6a7b8c9d0"
down_revision: Union[str, Sequence[str], None] = "d4e5f6a7b8c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

DESK = [
    "sales:sale:create",
    "sales:sale:read",
    "sales:order:create",
    "sales:order:read",
    "sales:order:payment_add",
    "sales:order:invoice",
    "sales:invoice:read",
    "sales:customer:read",
    "sales:customer:write",
    "purchases:purchase:read",
]
BOSS = DESK + [
    "sales:sale:edit",
    "sales:order:update",
    "sales:order:delete",
    "sales:customer:delete",
    "purchases:purchase:create",
    "purchases:purchase:confirm",
]
GRANTS = {"manager": BOSS, "sales": DESK}


def upgrade() -> None:
    bind = op.get_bind()
    for key, actions in GRANTS.items():
        row = bind.execute(
            sa.text("SELECT id, document FROM iam_policies WHERE name = :n"),
            {"n": f"{key}-default"},
        ).first()
        if not row:
            continue
        doc = (
            row.document if isinstance(row.document, dict) else json.loads(row.document)
        )
        stmts = doc.setdefault("statements", [])
        if not stmts:
            stmts.append({"effect": "Allow", "actions": [], "resources": ["*"]})
        have = stmts[0]["actions"]
        have += [a for a in actions if a not in have]
        bind.execute(
            sa.text(
                "UPDATE iam_policies SET document = CAST(:d AS jsonb) WHERE id = :i"
            ),
            {"d": json.dumps(doc), "i": row.id},
        )


def downgrade() -> None:
    pass  # leaving extra actions on a role is harmless; nothing to undo
