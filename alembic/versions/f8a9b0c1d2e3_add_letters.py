"""Add official letters and the letterhead

Revision ID: f8a9b0c1d2e3
Revises: e7f8a9b0c1d2
Create Date: 2026-10-28 18:00:00.000000

Additive: tables letters and business_profile.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "f8a9b0c1d2e3"
down_revision: Union[str, Sequence[str], None] = "e7f8a9b0c1d2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _fk(table, name):
    return sa.Column(
        name,
        postgresql.UUID(as_uuid=True),
        sa.ForeignKey(f"{table}.id", ondelete="SET NULL"),
        nullable=True,
    )


def upgrade() -> None:
    op.create_table(
        "letters",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column("number", sa.String(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="issued"),
        sa.Column("issued_on", sa.Date(), nullable=False),
        sa.Column("subject", sa.String(), nullable=False),
        sa.Column("party_name", sa.String(), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=True),
        _fk("orders", "order_id"),
        _fk("items", "item_id"),
        _fk("restoration_projects", "restoration_id"),
        _fk("suppliers", "supplier_id"),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        _fk("users", "created_by"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("voided_at", sa.DateTime(timezone=True), nullable=True),
        _fk("users", "voided_by"),
        sa.Column("void_reason", sa.String(), nullable=True),
    )
    op.create_index("ix_letters_number", "letters", ["number"], unique=True)
    op.create_index("ix_letters_kind_issued", "letters", ["kind", "issued_on"])
    op.create_table(
        "business_profile",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("tagline", sa.String(), nullable=True),
        sa.Column("address", sa.Text(), nullable=True),
        sa.Column("phone", sa.String(), nullable=True),
        sa.Column("email", sa.String(), nullable=True),
        sa.Column("gstin", sa.String(), nullable=True),
        sa.Column("signatory_name", sa.String(), nullable=True),
        sa.Column("signatory_title", sa.String(), nullable=True),
        sa.Column("footer_note", sa.Text(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    # Supabase exposes the public schema through its REST API: keep these closed to it.
    for table in ("letters", "business_profile"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.drop_table("business_profile")
    op.drop_index("ix_letters_kind_issued", table_name="letters")
    op.drop_index("ix_letters_number", table_name="letters")
    op.drop_table("letters")
