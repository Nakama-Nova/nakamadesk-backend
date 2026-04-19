import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Numeric,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.db.base import Base


class Purchase(Base):
    """
    SQLAlchemy model for procurement transactions.

    Tracks purchases from suppliers, including invoice details, totals,
    and payment status.
    """

    __tablename__ = "purchases"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    invoice_number = Column(String, unique=True, index=True, nullable=True)
    purchase_date = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    supplier_id = Column(UUID(as_uuid=True), ForeignKey("suppliers.id"))
    total_amount = Column(Numeric(10, 2), default=0.0)
    tax_total = Column(Numeric(10, 2), default=0.0)

    # Day 2 additions — purchase lifecycle and type
    status = Column(String, default="pending")  # pending | confirmed | cancelled
    purchase_type = Column(
        String, default="tax_invoice"
    )  # tax_invoice | purchase_voucher
    is_itc_eligible = Column(Boolean, default=True)
    notes = Column(Text, nullable=True)

    payment_status = Column(String, default="pending")  # pending, paid, partial
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    items = relationship("PurchaseItem", back_populates="purchase", lazy="selectin")
    supplier = relationship("Supplier")
