import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, text
from sqlalchemy.dialects.postgresql import UUID

from app.db.base import Base


class ProductAsset(Base):
    """
    A file attached to a catalogue item: a photo, and later a .glb 3D model
    (or .stl/.nc for the CNC pipeline). `storage_key` is a path relative to
    the media store (see media_service), never a full URL, so the store can
    move (disk -> R2/S3) without touching the data.
    """

    __tablename__ = "product_assets"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    item_id = Column(
        UUID(as_uuid=True),
        ForeignKey("items.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    kind = Column(String, nullable=False)  # image | glb
    storage_key = Column(String, nullable=False)
    content_type = Column(String, nullable=False)
    bytes = Column(Integer, nullable=False)
    sort = Column(Integer, nullable=False, default=0)
    alt = Column(String, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
