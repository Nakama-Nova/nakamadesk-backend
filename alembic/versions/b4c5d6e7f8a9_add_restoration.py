"""Add restoration projects and files; scan_jobs.restoration_id

Revision ID: b4c5d6e7f8a9
Revises: a3b4c5d6e7f8
Create Date: 2026-10-27 20:00:00.000000

Additive: tables restoration_projects, restoration_files; scan_jobs.restoration_id.
Users get a new role value 'artisan' (a plain string column, nothing to alter).
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "b4c5d6e7f8a9"
down_revision: Union[str, Sequence[str], None] = "a3b4c5d6e7f8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _id():
    return sa.Column(
        "id",
        postgresql.UUID(as_uuid=True),
        server_default=sa.text("gen_random_uuid()"),
        primary_key=True,
    )


def _now(name):
    return sa.Column(
        name,
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("now()"),
    )


def _user(name):
    return sa.Column(
        name,
        postgresql.UUID(as_uuid=True),
        sa.ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )


def upgrade() -> None:
    op.create_table(
        "restoration_projects",
        _id(),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("temple_name", sa.String(), nullable=False),
        sa.Column("location", sa.String(), nullable=True),
        sa.Column("piece", sa.String(), nullable=True),
        sa.Column("condition_notes", sa.Text(), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="planned"),
        _user("lead_artisan_id"),
        _user("created_by"),
        _now("created_at"),
        _now("updated_at"),
    )
    op.create_table(
        "restoration_files",
        _id(),
        sa.Column(
            "restoration_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("restoration_projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "scan_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("scan_jobs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("key", sa.String(), nullable=False, unique=True),
        sa.Column("content_type", sa.String(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("label", sa.String(), nullable=True),
        _now("created_at"),
    )
    op.create_index(
        "ix_restoration_files_project", "restoration_files", ["restoration_id"]
    )
    op.add_column(
        "scan_jobs",
        sa.Column(
            "restoration_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("restoration_projects.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    # Supabase exposes the public schema through its REST API: keep these closed to it.
    for table in ("restoration_projects", "restoration_files"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.drop_column("scan_jobs", "restoration_id")
    op.drop_index("ix_restoration_files_project", table_name="restoration_files")
    op.drop_table("restoration_files")
    op.drop_table("restoration_projects")
