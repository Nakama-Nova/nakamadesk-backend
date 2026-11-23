"""Grant production, inventory, machines and plant actions to the built-in roles

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-11-24 09:00:00.000000

Data only, safe to run twice; same idea as e5f6a7b8c9d0. The lists are frozen here.
"""

import json
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "f6a7b8c9d0e1"
down_revision: Union[str, Sequence[str], None] = "e5f6a7b8c9d0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

GRANTS = {
    "manager": [
        "production:job:create",
        "production:job:read",
        "production:job:assign",
        "production:job:run",
        "bom:entry:read",
        "items:item:read",
        "items:cost:read",
        "items:item:write",
        "items:stock:adjust",
        "inventory:raw_material:read",
        "inventory:raw_material:write",
        "inventory:raw_material:adjust",
        "inventory:stock_location:view",
        "inventory:stock_location:transfer",
        "inventory:master:read",
        "inventory:master:write",
        "machines:machine:read",
        "machines:machine:write",
        "machines:tool:view",
        "machines:tool:manage",
    ],
    "sales": ["items:item:read", "inventory:stock_location:view"],
    "achari": [
        "production:job:read",
        "bom:entry:read",
        "bom:entry:write",
        "items:item:read",
        "machines:tool:view",
    ],
    "worker": ["items:item:read"],
    "artisan": ["items:item:read"],
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
