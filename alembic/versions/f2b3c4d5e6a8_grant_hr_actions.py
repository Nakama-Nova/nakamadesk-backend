"""Grant the HR actions to the built-in roles

Revision ID: f2b3c4d5e6a8
Revises: f1a2b3c4d5e7
Create Date: 2026-11-24 18:05:00.000000

Data only, safe to run twice. Everyone with an account may ask for leave; the manager
runs leave, shifts and holidays and reads wage history and staff documents. Changing
pay, deleting documents, leave types and offboarding stay with the owner (who holds *).
The lists are frozen here on purpose: later changes to app/iam/defaults.py must not
rewrite what this migration did.
"""

import json
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "f2b3c4d5e6a8"
down_revision: Union[str, Sequence[str], None] = "f1a2b3c4d5e7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SELF = ["leave:request:create"]
BOSS = SELF + [
    "leave:request:read_all",
    "leave:request:approve",
    "leave:balance:read_all",
    "shifts:shift:manage",
    "holidays:holiday:manage",
    "people:wage:history_read",
    "people:user:read_documents",
]
GRANTS = {
    "manager": BOSS,
    "accountant": SELF,
    "sales": SELF,
    "achari": SELF,
    "worker": SELF,
    "artisan": SELF,
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
    pass  # leaving extra actions on a role is harmless; nothing to undo
