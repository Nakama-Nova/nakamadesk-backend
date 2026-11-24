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


class SalePayment(Base):
    """
    Money received against a counter bill. A bill can take several (split payment,
    instalments). Sale.payment_status / payment_method are derived from the live rows.
    A wrong row is voided (kept, with the reason), never deleted. Mirrors OrderPayment.
    """

    __tablename__ = "sale_payments"
    __table_args__ = (Index("ix_sale_payments_sale", "sale_id", "paid_on"),)

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    sale_id = Column(
        UUID(as_uuid=True), ForeignKey("sales.id", ondelete="CASCADE"), nullable=False
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

    voided_at = Column(DateTime(timezone=True), nullable=True)
    voided_by = Column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    void_reason = Column(String, nullable=True)

    sale = relationship("Sale", back_populates="payments")
