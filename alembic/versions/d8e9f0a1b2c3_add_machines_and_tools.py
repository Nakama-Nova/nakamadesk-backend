"""machines (with maintenance and downtime) and tools (with issue and return)

Additive only: five new tables, nothing existing changes.

Revision ID: d8e9f0a1b2c3
Revises: c7d8e9f0a1b2
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "d8e9f0a1b2c3"
down_revision: Union[str, Sequence[str], None] = "c7d8e9f0a1b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

ID = dict(server_default=sa.text("gen_random_uuid()"), nullable=False)


def upgrade() -> None:
    op.create_table(
        "machines",
        sa.Column("id", sa.UUID(), **ID),
        sa.Column("tag", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("make_model", sa.String(), nullable=True),
        sa.Column("serial_number", sa.String(), nullable=True),
        sa.Column("location_id", sa.UUID(), nullable=True),
        sa.Column("purchase_date", sa.Date(), nullable=True),
        sa.Column("purchase_cost", sa.Numeric(12, 2), nullable=True),
        sa.Column("useful_life_years", sa.Integer(), nullable=True),
        sa.Column(
            "salvage_value", sa.Numeric(12, 2), server_default="0", nullable=False
        ),
        sa.Column("maintenance_every_days", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(), server_default="active", nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "status IN ('active', 'down', 'retired')", name="ck_machines_status"
        ),
        sa.CheckConstraint("purchase_cost >= 0", name="ck_machines_cost"),
        sa.ForeignKeyConstraint(["location_id"], ["plant_units.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tag"),
    )
    op.create_index("ix_machines_location_id", "machines", ["location_id"])

    op.create_table(
        "machine_maintenance",
        sa.Column("id", sa.UUID(), **ID),
        sa.Column("machine_id", sa.UUID(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("performed_on", sa.Date(), nullable=False),
        sa.Column("cost", sa.Numeric(12, 2), server_default="0", nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("done_by", sa.String(), nullable=True),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "kind IN ('scheduled', 'repair')", name="ck_machine_maint_kind"
        ),
        sa.CheckConstraint("cost >= 0", name="ck_machine_maint_cost"),
        sa.ForeignKeyConstraint(["machine_id"], ["machines.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_machine_maintenance_machine_id", "machine_maintenance", ["machine_id"]
    )

    op.create_table(
        "machine_downtime",
        sa.Column("id", sa.UUID(), **ID),
        sa.Column("machine_id", sa.UUID(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("ended_at", sa.DateTime(), nullable=True),
        sa.Column("reason", sa.String(), nullable=True),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.ForeignKeyConstraint(["machine_id"], ["machines.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_machine_downtime_machine_id", "machine_downtime", ["machine_id"]
    )

    op.create_table(
        "tools",
        sa.Column("id", sa.UUID(), **ID),
        sa.Column("tag", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("category", sa.String(), nullable=True),
        sa.Column("location_id", sa.UUID(), nullable=True),
        sa.Column("purchase_date", sa.Date(), nullable=True),
        sa.Column("purchase_cost", sa.Numeric(12, 2), nullable=True),
        sa.Column("status", sa.String(), server_default="available", nullable=False),
        sa.Column("condition", sa.String(), server_default="good", nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "status IN ('available', 'issued', 'lost', 'broken', 'retired')",
            name="ck_tools_status",
        ),
        sa.CheckConstraint(
            "condition IN ('new', 'good', 'worn', 'damaged')", name="ck_tools_condition"
        ),
        sa.ForeignKeyConstraint(["location_id"], ["plant_units.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tag"),
    )
    op.create_index("ix_tools_location_id", "tools", ["location_id"])

    op.create_table(
        "tool_issues",
        sa.Column("id", sa.UUID(), **ID),
        sa.Column("tool_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("issued_by", sa.UUID(), nullable=True),
        sa.Column("issued_at", sa.DateTime(), nullable=False),
        sa.Column("condition_out", sa.String(), nullable=False),
        sa.Column("returned_at", sa.DateTime(), nullable=True),
        sa.Column("outcome", sa.String(), nullable=True),
        sa.Column("condition_in", sa.String(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "outcome IS NULL OR outcome IN ('ok', 'damaged', 'lost')",
            name="ck_tool_issues_outcome",
        ),
        sa.ForeignKeyConstraint(["tool_id"], ["tools.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["issued_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_tool_issues_user_id", "tool_issues", ["user_id"])
    op.create_index(
        "ix_tool_issues_tool_open", "tool_issues", ["tool_id", "returned_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_tool_issues_tool_open", table_name="tool_issues")
    op.drop_index("ix_tool_issues_user_id", table_name="tool_issues")
    op.drop_table("tool_issues")
    op.drop_index("ix_tools_location_id", table_name="tools")
    op.drop_table("tools")
    op.drop_index("ix_machine_downtime_machine_id", table_name="machine_downtime")
    op.drop_table("machine_downtime")
    op.drop_index("ix_machine_maintenance_machine_id", table_name="machine_maintenance")
    op.drop_table("machine_maintenance")
    op.drop_index("ix_machines_location_id", table_name="machines")
    op.drop_table("machines")
