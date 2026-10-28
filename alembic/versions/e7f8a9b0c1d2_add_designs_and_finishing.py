"""Add designs, finishing projects, restoration expenses and artisan-only scans

Revision ID: e7f8a9b0c1d2
Revises: d6e7f8a9b0c1
Create Date: 2026-10-28 15:00:00.000000

Additive: tables designs and design_products; restoration_projects.kind / item_id /
supplier_id (and temple_name becomes optional, a finishing project has no temple);
expenses.restoration_id; scan_jobs.artisan_only (true for every existing restoration scan).
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "e7f8a9b0c1d2"
down_revision: Union[str, Sequence[str], None] = "d6e7f8a9b0c1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _fk(table, name, nullable=True, on_delete="SET NULL"):
    return sa.Column(
        name,
        postgresql.UUID(as_uuid=True),
        sa.ForeignKey(f"{table}.id", ondelete=on_delete),
        nullable=nullable,
    )


def upgrade() -> None:
    op.create_table(
        "designs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column("sku", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        _fk("restoration_projects", "restoration_id"),
        sa.Column("status", sa.String(), nullable=False, server_default="draft"),
        _fk("users", "created_by"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_designs_sku", "designs", ["sku"], unique=True)
    op.create_index("ix_designs_restoration_id", "designs", ["restoration_id"])
    op.create_table(
        "design_products",
        sa.Column(
            "design_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("designs.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "item_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("items.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_design_products_item_id", "design_products", ["item_id"])

    op.add_column("restoration_projects", sa.Column("kind", sa.String(), nullable=False, server_default="temple"))
    op.add_column("restoration_projects", _fk("items", "item_id"))
    op.add_column("restoration_projects", _fk("suppliers", "supplier_id"))
    op.alter_column("restoration_projects", "temple_name", existing_type=sa.String(), nullable=True)

    op.add_column("expenses", _fk("restoration_projects", "restoration_id"))
    op.create_index("ix_expenses_restoration_id", "expenses", ["restoration_id"])

    op.add_column("scan_jobs", sa.Column("artisan_only", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.execute("UPDATE scan_jobs SET artisan_only = true WHERE restoration_id IS NOT NULL")

    # Supabase exposes the public schema through its REST API: keep these closed to it.
    for table in ("designs", "design_products"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.drop_column("scan_jobs", "artisan_only")
    op.drop_index("ix_expenses_restoration_id", table_name="expenses")
    op.drop_column("expenses", "restoration_id")
    op.execute("UPDATE restoration_projects SET temple_name = '' WHERE temple_name IS NULL")
    op.alter_column("restoration_projects", "temple_name", existing_type=sa.String(), nullable=False)
    op.drop_column("restoration_projects", "supplier_id")
    op.drop_column("restoration_projects", "item_id")
    op.drop_column("restoration_projects", "kind")
    op.drop_index("ix_design_products_item_id", table_name="design_products")
    op.drop_table("design_products")
    op.drop_index("ix_designs_restoration_id", table_name="designs")
    op.drop_index("ix_designs_sku", table_name="designs")
    op.drop_table("designs")
