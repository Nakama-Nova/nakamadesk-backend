from datetime import datetime, timezone
from sqlalchemy import Column, DateTime, String, text, ForeignKey
from sqlalchemy.dialects.postgresql import UUID, JSONB
import uuid

from app.db.base import Base


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    table_name = Column(String, index=True)
    row_id = Column(String, index=True)
    action = Column(String)  # INSERT, UPDATE, DELETE
    old_data = Column(JSONB, nullable=True)
    new_data = Column(JSONB, nullable=True)
    timestamp = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)
