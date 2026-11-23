import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Index, String, Text, text
from sqlalchemy.dialects.postgresql import UUID

from app.db.base import Base


def _now() -> datetime:
    """Naive UTC, like the other timestamp columns here."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class CustomerComment(Base):
    """
    What a customer wrote about their order, for the website. One per order (the unique
    `order_id`), and only someone who knows the order number AND its phone can send it.
    `status`: pending (just sent, nothing shows), approved (shown on the website), hidden
    (staff removed it, or it never passed). A comment is never shown until staff approve it.
    `display_name` and `place` are what the customer chose to have shown, with their consent.
    """

    __tablename__ = "customer_comments"
    __table_args__ = (
        Index("ix_customer_comments_status_created", "status", "created_at"),
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
        unique=True,
    )
    display_name = Column(String(60), nullable=False)
    place = Column(String(60), nullable=True)
    piece = Column(String(150), nullable=True)  # the first thing on the order
    body = Column(Text, nullable=False)
    status = Column(String(20), nullable=False, default="pending")
    created_at = Column(DateTime, nullable=False, default=_now)
    decided_at = Column(DateTime, nullable=True)
    decided_by = Column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
