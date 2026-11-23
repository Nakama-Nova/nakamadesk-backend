"""Grant letters, budgets, reports and dashboard actions to the built-in roles

Revision ID: c0d1e2f3a4b5
Revises: b9c0d1e2f3a4
Create Date: 2026-11-24 12:00:00.000000

Data only, safe to run twice; same idea as the earlier grant migrations. Frozen lists.
"""

import json
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "c0d1e2f3a4b5"
down_revision: Union[str, Sequence[str], None] = "b9c0d1e2f3a4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

DESK = ["letters:quotation:issue", "letters:order_confirmation:issue"]
GRANTS = {
    "manager": DESK
    + [
        "letters:purchase_receipt:issue",
        "budgets:budget:read",
        "reports:report:read",
        "reports:dashboard:read",
    ],
    "sales": DESK,
}


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
    pass  # extra actions on a role are harmless; nothing to undo
