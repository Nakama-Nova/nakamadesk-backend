import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import UUID

from app.db.base import Base

STATUSES = (
    "planned",  # agreed, nothing captured yet
    "capturing",  # photos/video being taken at the temple
    "processing",  # the workshop PC is building the 3D model
    "review",  # results are in; artisans check them
    "carving",  # being recarved or repaired
    "finishing",  # a bought piece being fine-finished
    "painting",  # ... and painted
    "restored",  # finished (for a bought piece: ready to sell)
    "on_hold",
    "cancelled",
)


class RestorationProject(Base):
    """
    One old temple carving being documented and restored: which temple, which piece,
    its condition and where the work stands. Visible only to the owner and artisans.
    Scans (photos/video) attach through scan_jobs.restoration_id.
    """

    __tablename__ = "restoration_projects"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    # temple = restoring a piece of a temple; finishing = a piece bought from another
    # artisan that is being fine-finished and painted (item_id is that piece)
    kind = Column(String, nullable=False, default="temple", server_default="temple")
    item_id = Column(
        UUID(as_uuid=True), ForeignKey("items.id", ondelete="SET NULL"), nullable=True
    )
    supplier_id = Column(
        UUID(as_uuid=True),
        ForeignKey("suppliers.id", ondelete="SET NULL"),
        nullable=True,
    )
    title = Column(String, nullable=False)
    temple_name = Column(String, nullable=True)  # required for temple projects only
    location = Column(String, nullable=True)
    piece = Column(String, nullable=True)  # e.g. "Dwarapalaka door panel, left"
    condition_notes = Column(Text, nullable=True)
    status = Column(String, nullable=False, default="planned", server_default="planned")
    lead_artisan_id = Column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_by = Column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=text("now()"),
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        server_default=text("now()"),
    )


class RestorationFile(Base):
    """A result of a restoration scan (3D model, render, G-code), kept in the private store."""

    __tablename__ = "restoration_files"
    __table_args__ = (Index("ix_restoration_files_project", "restoration_id"),)

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    restoration_id = Column(
        UUID(as_uuid=True),
        ForeignKey("restoration_projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    scan_id = Column(
        UUID(as_uuid=True),
        ForeignKey("scan_jobs.id", ondelete="SET NULL"),
        nullable=True,
    )
    kind = Column(String, nullable=False)  # render | glb | stl | obj | ply | nc
    key = Column(String, nullable=False, unique=True)
    content_type = Column(String, nullable=False)
    size_bytes = Column(BigInteger, nullable=False, default=0, server_default="0")
    label = Column(String, nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=text("now()"),
    )
