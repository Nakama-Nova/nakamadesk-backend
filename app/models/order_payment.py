import uuid
from datetime import date, datetime, timezone

from sqlalchemy import (
    Column,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.db.base import Base


class OrderPayment(Base):
    """
    Money received against an order: the advance, instalments and the final balance.
    Orders.advance_paid is the running total of these rows, so every rupee has a date,
    a method and who took it. Rows are never edited or deleted.
    """

    __tablename__ = "order_payments"
    __table_args__ = (Index("ix_order_payments_order", "order_id", "paid_on"),)

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
    amount = Column(Numeric(12, 2), nullable=False)
    method = Column(String, nullable=False, default="cash", server_default="cash")
    reference = Column(String, nullable=True)  # UPI id, cheque number...
    note = Column(String, nullable=True)
    paid_on = Column(Date, nullable=False, default=date.today)
    created_by = Column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=text("now()"),
    )

    order = relationship("Order", back_populates="payments")
