"""Link a design to the flat-carving scan it was made from (3D Lab)

Revision ID: c6f9e3a1d8b4
Revises: b5e8d2f0c3a7
Create Date: 2026-11-04 21:30:00.000000

Additive: designs.scan_id (nullable, ON DELETE SET NULL).
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "c6f9e3a1d8b4"
down_revision: Union[str, Sequence[str], None] = "b5e8d2f0c3a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("designs", sa.Column("scan_id", sa.UUID(), nullable=True))
    op.create_foreign_key(
        "fk_designs_scan_id",
        "designs",
        "scan_jobs",
        ["scan_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_designs_scan_id", "designs", ["scan_id"])


def downgrade() -> None:
    op.drop_index("ix_designs_scan_id", table_name="designs")
    op.drop_constraint("fk_designs_scan_id", "designs", type_="foreignkey")
    op.drop_column("designs", "scan_id")
