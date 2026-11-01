"""drop items.brand_id and the brands table

Destructive, and the second half of removing brands: the code stopped using them in
the previous release (PR 67), so nothing still running reads or writes these. Run it
only once that release is live. Downgrade brings the empty table and column back; the
brand rows themselves are not restored.

Revision ID: e3f4a5b6c7d8
Revises: d2e3f4a5b6c7
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "e3f4a5b6c7d8"
down_revision: Union[str, Sequence[str], None] = "d2e3f4a5b6c7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column("items", "brand_id")  # takes its foreign key with it
    op.drop_table("brands")


def downgrade() -> None:
    op.create_table(
        "brands",
        sa.Column(
            "id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False
        ),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("description", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_brands_name", "brands", ["name"], unique=True)
    op.add_column("items", sa.Column("brand_id", sa.UUID(), nullable=True))
    op.create_foreign_key(
        "items_brand_id_fkey", "items", "brands", ["brand_id"], ["id"]
    )
