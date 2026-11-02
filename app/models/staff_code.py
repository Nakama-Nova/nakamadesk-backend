import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, String, text
from sqlalchemy.dialects.postgresql import UUID

from app.db.base import Base


def _now() -> datetime:
    """Naive UTC, like the other timestamp columns here."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class StaffCode(Base):
    """
    A one-time code emailed to a staff member so they can set their own password: the
    invite sent when the owner adds them, or the reset behind "Forgot password". Only a
    keyed hash is stored. A code works once, for a limited time, after a few wrong tries
    at most.
    """

    __tablename__ = "staff_codes"
    __table_args__ = (Index("ix_staff_codes_user_created", "user_id", "created_at"),)

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    purpose = Column(String, nullable=False)  # "invite" or "reset"
    code_hash = Column(String, nullable=False)
    expires_at = Column(DateTime, nullable=False)
    attempts = Column(Integer, nullable=False, default=0, server_default=text("0"))
    used_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=_now)
