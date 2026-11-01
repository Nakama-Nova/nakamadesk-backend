"""add plant_units and where an attendance row was marked

Additive only, so the old code keeps working during a deploy: a new table, and
nullable columns on attendance that old code never reads or writes.

Revision ID: d2e3f4a5b6c7
Revises: c1d2e3f4a5b6
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "d2e3f4a5b6c7"
down_revision: Union[str, Sequence[str], None] = "c1d2e3f4a5b6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "plant_units",
        sa.Column(
            "id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False
        ),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("code", sa.String(), nullable=False),
        sa.Column("latitude", sa.Float(), nullable=False),
        sa.Column("longitude", sa.Float(), nullable=False),
        sa.Column("radius_m", sa.Float(), nullable=False),
        sa.Column(
            "is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint("latitude BETWEEN -90 AND 90", name="ck_plant_units_lat"),
        sa.CheckConstraint("longitude BETWEEN -180 AND 180", name="ck_plant_units_lon"),
        sa.CheckConstraint("radius_m > 0", name="ck_plant_units_radius"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
        sa.UniqueConstraint("code"),
    )
    op.add_column("attendance", sa.Column("plant_unit_id", sa.UUID(), nullable=True))
    op.add_column("attendance", sa.Column("latitude", sa.Float(), nullable=True))
    op.add_column("attendance", sa.Column("longitude", sa.Float(), nullable=True))
    op.add_column("attendance", sa.Column("accuracy_m", sa.Float(), nullable=True))
    op.add_column(
        "attendance", sa.Column("location_status", sa.String(), nullable=True)
    )
    op.create_foreign_key(
        "fk_attendance_plant_unit_id",
        "attendance",
        "plant_units",
        ["plant_unit_id"],
        ["id"],
    )
    op.create_index("ix_attendance_plant_unit_id", "attendance", ["plant_unit_id"])


def downgrade() -> None:
    op.drop_index("ix_attendance_plant_unit_id", table_name="attendance")
    op.drop_constraint("fk_attendance_plant_unit_id", "attendance", type_="foreignkey")
    for col in (
        "location_status",
        "accuracy_m",
        "longitude",
        "latitude",
        "plant_unit_id",
    ):
        op.drop_column("attendance", col)
    op.drop_table("plant_units")
