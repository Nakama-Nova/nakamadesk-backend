import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID

from app.db.base import Base

DESIGN_STATUSES = ("draft", "ready", "retired")
# furniture = carved onto furniture parts in the 3D Lab; general = everything else
DESIGN_CATEGORIES = ("general", "furniture")


class Design(Base):
    """
    A reusable design (a pattern to carve), with its own code (DES-0001). It can come
    from a restoration (the scan and 3D files of the old piece) or be made fresh, and it
    can build several sculpture products; a product points back to its designs through
    design_products. Visible only to the owner and artisans, like restorations.
    """

    __tablename__ = "designs"
    __table_args__ = (UniqueConstraint("image_sha256", name="uq_designs_image_sha256"),)

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    sku = Column(String, unique=True, index=True, nullable=False)
    name = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    restoration_id = Column(
        UUID(as_uuid=True),
        ForeignKey("restoration_projects.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    status = Column(String, nullable=False, default="draft", server_default="draft")
    category = Column(
        String, nullable=False, default="general", server_default="general"
    )
    # The design's picture (what the Lab shows on a furniture part). It is kept in the
    # PRIVATE store (key under private/designs/) and never served by /media.
    # image_sha256 makes a picture be stored once: the same file cannot be added twice.
    image_key = Column(String, nullable=True)
    image_type = Column(String, nullable=True)
    image_sha256 = Column(String(64), nullable=True)
    # the flat-carving scan this design was made from (its depth picture is the design's
    # picture, and its G-code and 3D files are the files to carve from)
    scan_id = Column(
        UUID(as_uuid=True),
        ForeignKey("scan_jobs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
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


class DesignProduct(Base):
    """Which products are built from which design (many to many, seen from either side)."""

    __tablename__ = "design_products"

    design_id = Column(
        UUID(as_uuid=True),
        ForeignKey("designs.id", ondelete="CASCADE"),
        primary_key=True,
    )
    item_id = Column(
        UUID(as_uuid=True),
        ForeignKey("items.id", ondelete="CASCADE"),
        primary_key=True,
        index=True,
    )
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=text("now()"),
    )
