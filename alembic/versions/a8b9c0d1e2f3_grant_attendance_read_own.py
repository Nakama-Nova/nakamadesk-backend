"""Grant attendance:record:read_own to the built-in roles

Revision ID: a8b9c0d1e2f3
Revises: f6a7b8c9d0e1
Create Date: 2026-11-24 10:00:00.000000

Data only, safe to run twice. Offline sync used to let every role except the accountant
pull its own attendance; that is now this permission. Frozen list, like the earlier
grant migrations.
"""

import json
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "a8b9c0d1e2f3"
down_revision: Union[str, Sequence[str], None] = "f6a7b8c9d0e1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

ACTION = "attendance:record:read_own"
ROLES = ["manager", "sales", "achari", "worker", "artisan"]


def upgrade() -> None:
    bind = op.get_bind()
    for key in ROLES:
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
        if ACTION not in stmts[0]["actions"]:
            stmts[0]["actions"].append(ACTION)
        bind.execute(
            sa.text(
                "UPDATE iam_policies SET document = CAST(:d AS jsonb) WHERE id = :i"
            ),
            {"d": json.dumps(doc), "i": row.id},
        )


def downgrade() -> None:
    pass  # an extra action on a role is harmless; nothing to undo
