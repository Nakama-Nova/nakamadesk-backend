"""locations: shop kind and till flag, stock levels, location on movements and sales

Additive only, so the old code keeps working during a deploy: new columns are
nullable or have defaults, and the new table is unused by old code. Existing stock is
not moved anywhere; it simply counts as unassigned until transferred to a location.

Revision ID: f4a5b6c7d8e9
Revises: e3f4a5b6c7d8
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "f4a5b6c7d8e9"
down_revision: Union[str, Sequence[str], None] = "e3f4a5b6c7d8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "plant_units",
        sa.Column("kind", sa.String(), server_default="plant", nullable=False),
    )
    op.add_column(
        "plant_units",
        sa.Column(
            "has_till", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
    )
    op.create_check_constraint(
        "ck_plant_units_kind", "plant_units", "kind IN ('plant', 'shop')"
    )

    op.create_table(
        "stock_levels",
        sa.Column(
            "id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False
        ),
        sa.Column("location_id", sa.UUID(), nullable=False),
        sa.Column("item_id", sa.UUID(), nullable=True),
        sa.Column("raw_material_id", sa.UUID(), nullable=True),
        sa.Column("quantity", sa.Numeric(12, 4), nullable=False),
        sa.CheckConstraint(
            "num_nonnulls(item_id, raw_material_id) = 1",
            name="ck_stock_levels_one_of",
        ),
        sa.ForeignKeyConstraint(["location_id"], ["plant_units.id"]),
        sa.ForeignKeyConstraint(["item_id"], ["items.id"]),
        sa.ForeignKeyConstraint(["raw_material_id"], ["raw_materials.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("location_id", "item_id", name="uq_stock_levels_item"),
        sa.UniqueConstraint(
            "location_id", "raw_material_id", name="uq_stock_levels_material"
        ),
    )
    op.create_index("ix_stock_levels_location_id", "stock_levels", ["location_id"])

    op.add_column(
        "inventory_movements", sa.Column("location_id", sa.UUID(), nullable=True)
    )
    op.add_column(
        "inventory_movements", sa.Column("transfer_id", sa.UUID(), nullable=True)
    )
    op.create_foreign_key(
        "fk_inventory_movements_location_id",
        "inventory_movements",
        "plant_units",
        ["location_id"],
        ["id"],
    )
    op.create_index(
        "ix_inventory_movements_location_id", "inventory_movements", ["location_id"]
    )
    op.create_index(
        "ix_inventory_movements_transfer_id", "inventory_movements", ["transfer_id"]
    )

    op.add_column("sales", sa.Column("location_id", sa.UUID(), nullable=True))
    op.create_foreign_key(
        "fk_sales_location_id", "sales", "plant_units", ["location_id"], ["id"]
    )
    op.create_index("ix_sales_location_id", "sales", ["location_id"])


def downgrade() -> None:
    op.drop_index("ix_sales_location_id", table_name="sales")
    op.drop_constraint("fk_sales_location_id", "sales", type_="foreignkey")
    op.drop_column("sales", "location_id")
    op.drop_index(
        "ix_inventory_movements_transfer_id", table_name="inventory_movements"
    )
    op.drop_index(
        "ix_inventory_movements_location_id", table_name="inventory_movements"
    )
    op.drop_constraint(
        "fk_inventory_movements_location_id", "inventory_movements", type_="foreignkey"
    )
    op.drop_column("inventory_movements", "transfer_id")
    op.drop_column("inventory_movements", "location_id")
    op.drop_index("ix_stock_levels_location_id", table_name="stock_levels")
    op.drop_table("stock_levels")
    op.drop_constraint("ck_plant_units_kind", "plant_units", type_="check")
    op.drop_column("plant_units", "has_till")
    op.drop_column("plant_units", "kind")
