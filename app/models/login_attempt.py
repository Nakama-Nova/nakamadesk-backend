import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Index, String, text
from sqlalchemy.dialects.postgresql import UUID

from app.db.base import Base


class LoginAttempt(Base):
    """
    One failed sign-in. Kept in the database (not in memory) so the limit holds
    when the server is split over several serverless instances or restarts.
    Cleared for that user and address on a successful sign-in.
    """

    __tablename__ = "login_attempts"
    __table_args__ = (
        Index("ix_login_attempts_username_at", "username", "at"),
        Index("ix_login_attempts_ip_at", "ip", "at"),
    )

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    username = Column(String, nullable=False)  # lower-cased
    ip = Column(String, nullable=False)
    at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=text("now()"),
    )
