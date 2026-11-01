"""add users.token_version (ends a user's sessions when bumped)

Additive only: existing rows get 0, which is what tokens issued before this
change are treated as carrying, so nobody is signed out by the deploy.

Revision ID: c1d2e3f4a5b6
Revises: b0c1d2e3f4a5
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "c1d2e3f4a5b6"
down_revision: Union[str, Sequence[str], None] = "b0c1d2e3f4a5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "token_version", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "token_version")
