"""Add who reports to whom, and the position at check-out

Revision ID: 6b3d9e1a4c72
Revises: 5f1c7a9e2b40
Create Date: 2026-11-02 12:00:00.000000

Additive: users.reports_to_id, and the five check-out position columns on attendance
(the check-in ones already exist).
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "6b3d9e1a4c72"
down_revision: Union[str, Sequence[str], None] = "5f1c7a9e2b40"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "reports_to_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("ix_users_reports_to_id", "users", ["reports_to_id"])
    op.add_column(
        "attendance",
        sa.Column(
            "check_out_plant_unit_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("plant_units.id"),
            nullable=True,
        ),
    )
    for name in ("latitude", "longitude", "accuracy_m"):
        op.add_column(
            "attendance", sa.Column(f"check_out_{name}", sa.Float(), nullable=True)
        )
    op.add_column(
        "attendance", sa.Column("check_out_location_status", sa.String(), nullable=True)
    )


def downgrade() -> None:
    for name in (
        "check_out_location_status",
        "check_out_accuracy_m",
        "check_out_longitude",
        "check_out_latitude",
        "check_out_plant_unit_id",
    ):
        op.drop_column("attendance", name)
    op.drop_index("ix_users_reports_to_id", table_name="users")
    op.drop_column("users", "reports_to_id")
