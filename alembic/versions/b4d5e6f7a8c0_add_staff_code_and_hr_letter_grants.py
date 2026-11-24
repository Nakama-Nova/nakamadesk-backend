"""Staff numbers, and the HR letter and ID card actions for the manager

Revision ID: b4d5e6f7a8c0
Revises: a3c4d5e6f7b9
Create Date: 2026-11-24 22:00:00.000000

Adds users.staff_code (VS-0001, from initials of the letterhead name) with the counter
behind it, numbers everyone already on the staff list in the order they were added, and
lets the built-in manager role issue payslips and make ID cards. The offer letter and the
experience letter stay with the owner (who holds every action). Safe to run twice for the
grants. The action list is frozen here on purpose.
"""

import json
import re
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "b4d5e6f7a8c0"
down_revision: Union[str, Sequence[str], None] = "a3c4d5e6f7b9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

MANAGER_ACTIONS = ["letters:payslip:issue", "people:user:id_card"]


def upgrade() -> None:
    bind = op.get_bind()
    op.add_column("users", sa.Column("staff_code", sa.String(), nullable=True))
    op.create_index("ix_users_staff_code", "users", ["staff_code"], unique=True)
    op.execute("CREATE SEQUENCE IF NOT EXISTS staff_code_seq")

    name = bind.execute(
        sa.text("SELECT name FROM business_profile WHERE id = 1")
    ).scalar()
    initials = "".join(w[0] for w in re.findall(r"[A-Za-z]+", name or "Vriksha Studios")[:3]).upper() or "ST"  # fmt: skip
    ids = [r[0] for r in bind.execute(sa.text("SELECT id FROM users ORDER BY created_at, id"))]  # fmt: skip
    for n, user_id in enumerate(ids, 1):
        bind.execute(
            sa.text("UPDATE users SET staff_code = :c WHERE id = :i"),
            {"c": f"{initials}-{n:04d}", "i": user_id},
        )
    bind.execute(
        sa.text("SELECT setval('staff_code_seq', :n, :called)"),
        {"n": max(len(ids), 1), "called": bool(ids)},
    )

    row = bind.execute(
        sa.text("SELECT id, document FROM iam_policies WHERE name = 'manager-default'")
    ).first()
    if row:
        doc = (
            row.document if isinstance(row.document, dict) else json.loads(row.document)
        )
        stmts = doc.setdefault("statements", [])
        if not stmts:
            stmts.append({"effect": "Allow", "actions": [], "resources": ["*"]})
        have = stmts[0]["actions"]
        have += [a for a in MANAGER_ACTIONS if a not in have]
        bind.execute(
            sa.text(
                "UPDATE iam_policies SET document = CAST(:d AS jsonb) WHERE id = :i"
            ),
            {"d": json.dumps(doc), "i": row.id},
        )


def downgrade() -> None:
    op.drop_index("ix_users_staff_code", table_name="users")
    op.drop_column("users", "staff_code")
    op.execute("DROP SEQUENCE IF EXISTS staff_code_seq")
