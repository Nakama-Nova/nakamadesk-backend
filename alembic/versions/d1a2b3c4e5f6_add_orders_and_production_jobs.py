"""add_orders_and_production_jobs - Day 1 feature expansion

Revision ID: d1a2b3c4e5f6
Revises: 7aa338e58096
Create Date: 2026-04-15 20:52:00.000000

Adds four new tables for the Order Management + Production base structure:
  - orders
  - order_items
  - production_jobs
  - job_worker_assignments

NO existing tables are modified. This migration is fully additive and safe
to apply to a live system.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d1a2b3c4e5f6"
down_revision: Union[str, Sequence[str], None] = "7aa338e58096"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create orders, order_items, production_jobs, and job_worker_assignments tables."""

    # ------------------------------------------------------------------
    # Table: orders
    # ------------------------------------------------------------------
    op.create_table(
        "orders",
        sa.Column(
            "id",
            sa.UUID(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("order_number", sa.String(), nullable=False),
        sa.Column("customer_id", sa.UUID(), nullable=True),
        sa.Column("order_type", sa.String(), nullable=False, server_default="standard"),
        sa.Column("status", sa.String(), nullable=False, server_default="draft"),
        sa.Column("custom_specs", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("reference_image_url", sa.String(), nullable=True),
        sa.Column("estimated_amount", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column("advance_paid", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column("final_amount", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column("order_date", sa.DateTime(), nullable=False),
        sa.Column("expected_delivery", sa.Date(), nullable=True),
        sa.Column("delivered_at", sa.DateTime(), nullable=True),
        sa.Column("sale_id", sa.UUID(), nullable=True),
        sa.Column("created_by", sa.UUID(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["sale_id"], ["sales.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_orders_order_number"), "orders", ["order_number"], unique=True)
    op.create_index(op.f("ix_orders_customer_id"), "orders", ["customer_id"], unique=False)
    op.create_index(op.f("ix_orders_status"), "orders", ["status"], unique=False)

    # ------------------------------------------------------------------
    # Table: order_items
    # ------------------------------------------------------------------
    op.create_table(
        "order_items",
        sa.Column(
            "id",
            sa.UUID(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("order_id", sa.UUID(), nullable=False),
        sa.Column("item_id", sa.UUID(), nullable=True),
        sa.Column("item_name", sa.String(), nullable=True),
        sa.Column("quantity", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("unit_price", sa.Numeric(precision=12, scale=2), nullable=False, server_default="0"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["item_id"], ["items.id"]),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_order_items_order_id"), "order_items", ["order_id"], unique=False)

    # ------------------------------------------------------------------
    # Table: production_jobs
    # ------------------------------------------------------------------
    op.create_table(
        "production_jobs",
        sa.Column(
            "id",
            sa.UUID(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("job_number", sa.String(), nullable=False),
        sa.Column("order_id", sa.UUID(), nullable=True),
        sa.Column("item_id", sa.UUID(), nullable=True),
        sa.Column("custom_desc", sa.String(), nullable=True),
        sa.Column("target_quantity", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "produced_quantity",
            sa.Numeric(precision=10, scale=4),
            nullable=False,
            server_default="0",
        ),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("expected_by", sa.Date(), nullable=True),
        sa.Column("assigned_to", sa.UUID(), nullable=True),
        sa.Column("created_by", sa.UUID(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["assigned_to"], ["users.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["item_id"], ["items.id"]),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_production_jobs_job_number"), "production_jobs", ["job_number"], unique=True
    )
    op.create_index(
        op.f("ix_production_jobs_order_id"), "production_jobs", ["order_id"], unique=False
    )
    op.create_index(
        op.f("ix_production_jobs_status"), "production_jobs", ["status"], unique=False
    )
    op.create_index(
        op.f("ix_production_jobs_assigned_to"), "production_jobs", ["assigned_to"], unique=False
    )

    # ------------------------------------------------------------------
    # Table: job_worker_assignments
    # ------------------------------------------------------------------
    op.create_table(
        "job_worker_assignments",
        sa.Column(
            "id",
            sa.UUID(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("job_id", sa.UUID(), nullable=False),
        sa.Column("worker_id", sa.UUID(), nullable=False),
        sa.Column("role", sa.String(), nullable=True),
        sa.Column("assigned_at", sa.DateTime(), nullable=True),
        sa.Column("removed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["job_id"], ["production_jobs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["worker_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id", "worker_id", name="uq_job_worker"),
    )
    op.create_index(
        op.f("ix_job_worker_assignments_job_id"),
        "job_worker_assignments",
        ["job_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_job_worker_assignments_worker_id"),
        "job_worker_assignments",
        ["worker_id"],
        unique=False,
    )


def downgrade() -> None:
    """Drop all Day 1 tables in reverse dependency order."""
    op.drop_index(
        op.f("ix_job_worker_assignments_worker_id"), table_name="job_worker_assignments"
    )
    op.drop_index(
        op.f("ix_job_worker_assignments_job_id"), table_name="job_worker_assignments"
    )
    op.drop_table("job_worker_assignments")

    op.drop_index(op.f("ix_production_jobs_assigned_to"), table_name="production_jobs")
    op.drop_index(op.f("ix_production_jobs_status"), table_name="production_jobs")
    op.drop_index(op.f("ix_production_jobs_order_id"), table_name="production_jobs")
    op.drop_index(op.f("ix_production_jobs_job_number"), table_name="production_jobs")
    op.drop_table("production_jobs")

    op.drop_index(op.f("ix_order_items_order_id"), table_name="order_items")
    op.drop_table("order_items")

    op.drop_index(op.f("ix_orders_status"), table_name="orders")
    op.drop_index(op.f("ix_orders_customer_id"), table_name="orders")
    op.drop_index(op.f("ix_orders_order_number"), table_name="orders")
    op.drop_table("orders")
