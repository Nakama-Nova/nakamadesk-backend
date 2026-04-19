"""Day 2 — Inventory Movement Engine

Revision ID: e2f3a4b5c6d7
Revises: d1a2b3c4e5f6
Create Date: 2026-04-16 09:00:00.000000

Adds:
  - inventory_movements table (core audit ledger)
  - production_material_allocations table (per-job raw material consumption)
  - New columns on purchases: status, purchase_type, is_itc_eligible, notes
  - New columns on purchase_items: raw_material_id (nullable FK)

All changes are additive / backward-compatible. No existing data is modified.
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers
revision: str = "e2f3a4b5c6d7"
down_revision: Union[str, Sequence[str], None] = "d1a2b3c4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create inventory movement tables and extend purchase tables."""

    # ------------------------------------------------------------------
    # Table: inventory_movements
    # ------------------------------------------------------------------
    op.create_table(
        "inventory_movements",
        sa.Column(
            "id",
            sa.UUID(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("movement_type", sa.String(), nullable=False),
        sa.Column("reference_type", sa.String(), nullable=True),
        sa.Column("reference_id", sa.UUID(), nullable=True),
        sa.Column("raw_material_id", sa.UUID(), nullable=True),
        sa.Column("item_id", sa.UUID(), nullable=True),
        sa.Column("quantity", sa.Numeric(precision=10, scale=4), nullable=False),
        sa.Column("unit_cost", sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["item_id"], ["items.id"], name="fk_inv_mov_item"),
        sa.ForeignKeyConstraint(
            ["raw_material_id"],
            ["raw_materials.id"],
            name="fk_inv_mov_material",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_inv_mov_movement_type", "inventory_movements", ["movement_type"]
    )
    op.create_index(
        "ix_inv_mov_reference",
        "inventory_movements",
        ["reference_type", "reference_id"],
    )
    op.create_index(
        "ix_inv_mov_raw_material_id",
        "inventory_movements",
        ["raw_material_id"],
    )
    op.create_index("ix_inv_mov_item_id", "inventory_movements", ["item_id"])
    op.create_index("ix_inv_mov_created_at", "inventory_movements", ["created_at"])

    # ------------------------------------------------------------------
    # Table: production_material_allocations
    # ------------------------------------------------------------------
    op.create_table(
        "production_material_allocations",
        sa.Column(
            "id",
            sa.UUID(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("job_id", sa.UUID(), nullable=False),
        sa.Column("material_id", sa.UUID(), nullable=False),
        sa.Column("allocated_qty", sa.Numeric(precision=10, scale=4), nullable=False),
        sa.Column(
            "consumed_qty",
            sa.Numeric(precision=10, scale=4),
            nullable=True,
            server_default="0",
        ),
        sa.Column(
            "scrap_qty",
            sa.Numeric(precision=10, scale=4),
            nullable=True,
            server_default="0",
        ),
        sa.Column("allocated_by", sa.UUID(), nullable=True),
        sa.Column("allocated_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["allocated_by"], ["users.id"]),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["production_jobs.id"],
            ondelete="CASCADE",
            name="fk_pma_job",
        ),
        sa.ForeignKeyConstraint(
            ["material_id"],
            ["raw_materials.id"],
            name="fk_pma_material",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_pma_job_id", "production_material_allocations", ["job_id"])
    op.create_index(
        "ix_pma_material_id", "production_material_allocations", ["material_id"]
    )

    # ------------------------------------------------------------------
    # Extend: purchases table
    # ------------------------------------------------------------------
    op.add_column(
        "purchases",
        sa.Column("status", sa.String(), server_default="pending", nullable=True),
    )
    op.add_column(
        "purchases",
        sa.Column(
            "purchase_type",
            sa.String(),
            server_default="tax_invoice",
            nullable=True,
        ),
    )
    op.add_column(
        "purchases",
        sa.Column(
            "is_itc_eligible", sa.Boolean(), server_default="true", nullable=True
        ),
    )
    op.add_column("purchases", sa.Column("notes", sa.Text(), nullable=True))

    # invoice_number was NOT NULL — relax to nullable for purchase_vouchers
    op.alter_column("purchases", "invoice_number", nullable=True)

    # ------------------------------------------------------------------
    # Extend: purchase_items table
    # ------------------------------------------------------------------
    op.add_column(
        "purchase_items",
        sa.Column("raw_material_id", sa.UUID(), nullable=True),
    )
    op.create_foreign_key(
        "fk_purchase_items_raw_material",
        "purchase_items",
        "raw_materials",
        ["raw_material_id"],
        ["id"],
    )
    # make item_id nullable (either item_id OR raw_material_id is set)
    op.alter_column("purchase_items", "item_id", nullable=True)

    # change quantity type from Integer to Numeric for fractional quantities
    op.alter_column(
        "purchase_items",
        "quantity",
        type_=sa.Numeric(precision=10, scale=4),
        existing_type=sa.Integer(),
    )


def downgrade() -> None:
    """Reverse all Day 2 changes."""

    # Restore purchase_items
    op.alter_column(
        "purchase_items",
        "quantity",
        type_=sa.Integer(),
        existing_type=sa.Numeric(precision=10, scale=4),
    )
    op.alter_column("purchase_items", "item_id", nullable=False)
    op.drop_constraint(
        "fk_purchase_items_raw_material", "purchase_items", type_="foreignkey"
    )
    op.drop_column("purchase_items", "raw_material_id")

    # Restore purchases
    op.alter_column("purchases", "invoice_number", nullable=False)
    op.drop_column("purchases", "notes")
    op.drop_column("purchases", "is_itc_eligible")
    op.drop_column("purchases", "purchase_type")
    op.drop_column("purchases", "status")

    # Drop new tables
    op.drop_index("ix_pma_material_id", "production_material_allocations")
    op.drop_index("ix_pma_job_id", "production_material_allocations")
    op.drop_table("production_material_allocations")

    op.drop_index("ix_inv_mov_created_at", "inventory_movements")
    op.drop_index("ix_inv_mov_item_id", "inventory_movements")
    op.drop_index("ix_inv_mov_raw_material_id", "inventory_movements")
    op.drop_index("ix_inv_mov_reference", "inventory_movements")
    op.drop_index("ix_inv_mov_movement_type", "inventory_movements")
    op.drop_table("inventory_movements")
