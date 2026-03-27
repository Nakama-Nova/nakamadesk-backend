import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, String, text
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.db.base import Base


class AuditLog(Base):
    """
    SQLAlchemy model for system-wide audit trails.

    Captures changes to database records, including the action type (INSERT, UPDATE, DELETE),
    the user responsible, and a JSON snapshot of the old and new data.
    """

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
