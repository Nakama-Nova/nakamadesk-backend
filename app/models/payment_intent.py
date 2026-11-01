import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Numeric, String, text
from sqlalchemy.dialects.postgresql import UUID

from app.db.base import Base


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class PaymentIntent(Base):
    """
    One attempt to pay an order online. `provider_order_id` is the id Razorpay gave
    the payment order we created for it, which ties the browser's result and the
    webhook back to this row. Money is recorded on the order (order_payments) only
    when a payment against this intent is confirmed, never when it is created.

    status: created -> paid, or failed / mismatch (the amount paid was not the amount
    asked for, so nothing was credited and a person must look).
    """

    __tablename__ = "payment_intents"

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
        index=True,
    )
    provider = Column(String, nullable=False, default="razorpay")
    provider_order_id = Column(String, nullable=False, unique=True)
    provider_payment_id = Column(String, nullable=True)
    amount = Column(Numeric(12, 2), nullable=False)  # rupees
    currency = Column(String, nullable=False, default="INR")
    status = Column(String, nullable=False, default="created")
    created_at = Column(DateTime, nullable=False, default=_now)
    paid_at = Column(DateTime, nullable=True)
