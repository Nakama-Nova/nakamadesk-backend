"""Grant bill payment, return and credit note actions to the built-in roles

Revision ID: e2b3c4d5f6a7
Revises: e1a2b3c4d5f6
Create Date: 2026-11-24 18:05:00.000000

Data only, safe to run twice (adds only what is missing); same idea as
e5f6a7b8c9d0. The lists are frozen here on purpose. The owner has * already.
sales:payment:add and sales:credit_note:read go to manager and sales; sales:payment:void
and sales:sale:return to the manager only.
"""

import json
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "e2b3c4d5f6a7"
down_revision: Union[str, Sequence[str], None] = "e1a2b3c4d5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

DESK = ["sales:payment:add", "sales:credit_note:read"]
BOSS = DESK + ["sales:payment:void", "sales:sale:return"]
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
