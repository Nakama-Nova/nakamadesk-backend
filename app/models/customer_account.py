import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import UUID

from app.db.base import Base


def _now() -> datetime:
    """Naive UTC, like the other timestamp columns here."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class CustomerAccount(Base):
    """
    A shopper's sign-in on the website. Separate from `users` (staff) on purpose: a
    customer token can never open a staff route and a staff token is no customer login.
    There is no password: the shopper proves the email by typing a one-time code.

    `customer_id` will link the account to the phone-keyed `customers` row (walk-in and
    guest orders) once the phone is verified by SMS; until then the two stay apart.
    """

    __tablename__ = "customer_accounts"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    email = Column(String, unique=True, nullable=False)  # lower case
    email_verified_at = Column(DateTime, nullable=True)
    name = Column(String, nullable=True)
    phone = Column(String, nullable=True)  # as typed, NOT verified
    customer_id = Column(UUID(as_uuid=True), ForeignKey("customers.id"), nullable=True)
    terms_accepted_at = Column(DateTime, nullable=True)
    marketing_consent = Column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    is_active = Column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    # Raising it signs the shopper out everywhere (tokens carry the number they were
    # issued under).
    token_version = Column(Integer, nullable=False, default=0, server_default=text("0"))
    created_at = Column(DateTime, nullable=False, default=_now)
    last_login_at = Column(DateTime, nullable=True)


class LoginCode(Base):
    """
    A one-time sign-in code sent by email. Only a keyed hash of the code is stored.
    A code works once, for a few minutes, after a few wrong tries at most.
    """

    __tablename__ = "login_codes"
    __table_args__ = (Index("ix_login_codes_email_created", "email", "created_at"),)

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    email = Column(String, nullable=False)  # lower case
    code_hash = Column(String, nullable=False)
    expires_at = Column(DateTime, nullable=False)
    attempts = Column(Integer, nullable=False, default=0, server_default=text("0"))
    used_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=_now)
