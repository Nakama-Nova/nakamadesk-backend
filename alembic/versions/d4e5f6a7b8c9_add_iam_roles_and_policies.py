"""Add IAM roles and policies

Revision ID: d4e5f6a7b8c9
Revises: d0f6b2c4e8a5
Create Date: 2026-11-23 22:00:00.000000

Additive: four tables, plus the built-in roles (owner, manager, ...) as rows. users.role
is untouched and still names a person's main role.
"""

import json
import uuid
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "d4e5f6a7b8c9"
down_revision: Union[str, Sequence[str], None] = "d0f6b2c4e8a5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

UUID = postgresql.UUID(as_uuid=True)
GEN = sa.text("gen_random_uuid()")


def upgrade() -> None:
    from app.iam import defaults  # the built-ins live in one place

    op.create_table(
        "iam_policies",
        sa.Column("id", UUID, server_default=GEN, primary_key=True),
        sa.Column("name", sa.String, nullable=False, unique=True),
        sa.Column("document", postgresql.JSONB, nullable=False),
        sa.Column("is_system", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("created_by", UUID, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("updated_at", sa.DateTime),
    )
    op.create_table(
        "iam_roles",
        sa.Column("id", UUID, server_default=GEN, primary_key=True),
        sa.Column("key", sa.String, nullable=False, unique=True),
        sa.Column("label", sa.String, nullable=False),
        sa.Column("description", sa.Text),
        sa.Column("is_system", sa.Boolean, nullable=False, server_default="false"),
    )
    op.create_index("ix_iam_roles_key", "iam_roles", ["key"])
    op.create_table(
        "iam_role_policies",
        sa.Column(
            "role_id",
            UUID,
            sa.ForeignKey("iam_roles.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "policy_id",
            UUID,
            sa.ForeignKey("iam_policies.id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )
    op.create_table(
        "iam_user_roles",
        sa.Column("id", UUID, server_default=GEN, primary_key=True),
        sa.Column(
            "user_id",
            UUID,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "role_id",
            UUID,
            sa.ForeignKey("iam_roles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "plant_unit_id", UUID, sa.ForeignKey("plant_units.id", ondelete="CASCADE")
        ),
    )
    op.create_index("ix_iam_user_roles_user_id", "iam_user_roles", ["user_id"])

    bind = op.get_bind()
    for key, (label, statements) in defaults.SYSTEM_ROLES.items():
        role_id, policy_id = uuid.uuid4(), uuid.uuid4()
        bind.execute(
            sa.text(
                "INSERT INTO iam_roles (id, key, label, is_system) VALUES (:i, :k, :l, true)"
            ),
            {"i": role_id, "k": key, "l": label},
        )
        bind.execute(
            sa.text(
                "INSERT INTO iam_policies (id, name, document, is_system) "
                "VALUES (:i, :n, CAST(:d AS jsonb), true)"
            ),
            {
                "i": policy_id,
                "n": f"{key}-default",
                "d": json.dumps(defaults.document(statements)),
            },
        )
        bind.execute(
            sa.text(
                "INSERT INTO iam_role_policies (role_id, policy_id) VALUES (:r, :p)"
            ),
            {"r": role_id, "p": policy_id},
        )


def downgrade() -> None:
    op.drop_table("iam_user_roles")
    op.drop_table("iam_role_policies")
    op.drop_table("iam_roles")
    op.drop_table("iam_policies")
