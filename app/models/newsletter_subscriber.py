import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, String, text
from sqlalchemy.dialects.postgresql import UUID

from app.db.base import Base


def _now() -> datetime:
    """Naive UTC, like the other timestamp columns here."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class NewsletterSubscriber(Base):
    """
    One address that asked for the newsletter (double opt-in). The list the owner
    actually sends to lives in Brevo; this row is the record of consent: which wording
    they agreed to (`consent_version`), when they asked (`subscribed_at`) and when they
    confirmed or left. No IP address is kept.

    `status`: pending (asked, not yet confirmed), confirmed, unsubscribed. Signing up
    again after unsubscribing starts a new round: pending, with the times reset.
    Confirm and unsubscribe links are signed with this row's id, so no token is stored.
    """

    __tablename__ = "newsletter_subscribers"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    email = Column(String, unique=True, nullable=False)  # lower case
    status = Column(String(20), nullable=False, default="pending")
    source = Column(String(30), nullable=True)  # footer, journal, home...
    consent_version = Column(String(40), nullable=False)
    subscribed_at = Column(DateTime, nullable=False, default=_now)
    confirmed_at = Column(DateTime, nullable=True)
    unsubscribed_at = Column(DateTime, nullable=True)
    last_confirmation_sent_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=_now)
