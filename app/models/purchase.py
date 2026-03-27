from datetime import datetime, timezone
from sqlalchemy import Column, DateTime, ForeignKey, String, text, Numeric
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
import uuid

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
    invoice_number = Column(String, unique=True, index=True)
    purchase_date = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    supplier_id = Column(UUID(as_uuid=True), ForeignKey("suppliers.id"))
    total_amount = Column(Numeric(10, 2), default=0.0)
    tax_total = Column(Numeric(10, 2), default=0.0)

    payment_status = Column(String, default="pending")  # pending, paid, partial
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    items = relationship("PurchaseItem", back_populates="purchase")
    supplier = relationship("Supplier")
