import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Index, String, text
from sqlalchemy.dialects.postgresql import UUID

from app.db.base import Base


class OrderEmailLog(Base):
    """
    One row per order email we decided to send, written BEFORE the send so it doubles as
    the lock: the unique indexes below let exactly one request claim an (order, kind) or
    an (order, kind, payment), however many race. `status`: sending (claimed, the Brevo
    call is in flight; stays so only if the process died mid-send), sent, failed, or
    skipped (switched off, not set up, or over the per-address limit; `error` says why).
    Nothing is retried: a failed or skipped email is not sent later. Neither the email
    body nor Brevo's answer is kept, only a short reason.
    """

    __tablename__ = "order_email_log"
    __table_args__ = (
        # NULLs are distinct in a unique index, so two partial ones: one for the emails
        # that belong to the order, one for those that belong to a single payment.
        Index(
            "uq_order_email_order_kind",
            "order_id",
            "kind",
            unique=True,
            postgresql_where=text("payment_id IS NULL"),
        ),
        Index(
            "uq_order_email_payment_kind",
            "order_id",
            "kind",
            "payment_id",
            unique=True,
            postgresql_where=text("payment_id IS NOT NULL"),
        ),
        Index("ix_order_email_to_kind_created", "to_email", "kind", "created_at"),
    )

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    order_id = Column(
        UUID(as_uuid=True),
        ForeignKey("orders.id", ondelete="CASCADE"),
        nullable=False,
    )
    kind = Column(String(30), nullable=False)  # order_received | order_confirmed | ...
    # set for the payment_received kind: one email per payment
    payment_id = Column(
        UUID(as_uuid=True),
        ForeignKey("order_payments.id", ondelete="CASCADE"),
        nullable=True,
    )
    to_email = Column(String, nullable=False)
    status = Column(String(20), nullable=False)
    error = Column(String(200), nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=text("now()"),
    )
