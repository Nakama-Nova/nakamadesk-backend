import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.db.base import Base


class ScanJob(Base):
    """
    One 3D scan: photos or a video captured on a phone, reconstructed later on
    the workshop GPU PC. The table is the queue (see docs/design/scan-and-lab.md
    section 5.1): status created -> uploaded -> processing -> done, or failed /
    cancelled. A lease (claimed_by + lease_until) lets a dead PC release a job.
    """

    __tablename__ = "scan_jobs"
    __table_args__ = (Index("ix_scan_jobs_status_created", "status", "created_at"),)

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    item_id = Column(
        UUID(as_uuid=True), ForeignKey("items.id", ondelete="SET NULL"), nullable=True
    )
    created_by = Column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    status = Column(String, nullable=False, default="created", server_default="created")
    source_kind = Column(String, nullable=False)  # photos | video
    source_keys = Column(JSONB, nullable=False, default=list, server_default="[]")
    source_bytes = Column(BigInteger, nullable=False, default=0, server_default="0")
    params = Column(JSONB, nullable=False, default=dict, server_default="{}")
    claimed_by = Column(String, nullable=True)
    lease_until = Column(DateTime(timezone=True), nullable=True)
    attempts = Column(Integer, nullable=False, default=0, server_default="0")
    progress = Column(Integer, nullable=False, default=0, server_default="0")
    note = Column(String, nullable=True)  # what the PC is doing right now
    error = Column(String, nullable=True)
    result_asset_ids = Column(JSONB, nullable=False, default=list, server_default="[]")
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=text("now()"),
    )
    finished_at = Column(DateTime(timezone=True), nullable=True)
