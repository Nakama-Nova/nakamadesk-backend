"""Grant 3D Lab, restoration, scans, expenses and assets actions to the built-in roles

Revision ID: b9c0d1e2f3a4
Revises: a8b9c0d1e2f3
Create Date: 2026-11-24 11:00:00.000000

Data only, safe to run twice; same idea as the earlier grant migrations. Frozen lists.
"""

import json
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "b9c0d1e2f3a4"
down_revision: Union[str, Sequence[str], None] = "a8b9c0d1e2f3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

GRANTS = {
    "manager": [
        "lab:part:read",
        "lab:part:edit",
        "scans:job:work",
        "scans:job:process",
        "expenses:expense:write",
        "assets:file:read",
        "assets:file:read_all",
        "assets:file:write",
    ],
    "achari": ["lab:part:read"],
    "worker": ["lab:part:read"],
    "artisan": [
        "lab:part:read",
        "restoration:project:access",
        "scans:job:work",
        "assets:file:read",
    ],
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
