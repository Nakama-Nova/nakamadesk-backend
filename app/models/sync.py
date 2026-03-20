from sqlalchemy import Column, String, DateTime, text
from sqlalchemy.dialects.postgresql import UUID, JSONB
import uuid
from datetime import datetime, timezone

from app.db.base import Base


class SyncLog(Base):
    __tablename__ = "sync_logs"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    client_id = Column(
        String, index=True, nullable=False, unique=True
    )  # Used for idempotency mapping (incoming id)
    entity = Column(
        String, index=True, nullable=False
    )  # e.g. "sale", "item", "attendance"
    record_id = Column(
        UUID(as_uuid=True), index=True, nullable=False
    )  # Extracted target record
    action = Column(String, nullable=False)  # "create", "update", "delete"
    payload = Column(JSONB, nullable=True)  # The actual sync payload from client outbox
    status = Column(
        String, nullable=False, default="pending"
    )  # "success", "failed", "pending"
    error_message = Column(String, nullable=True)

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
