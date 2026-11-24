import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import Column, DateTime, ForeignKey, Numeric, String, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.db.base import Base


class Sale(Base):
    """
    SQLAlchemy model for sales transactions.

    The primary record for client transactions, storing invoice numbers,
    totals, taxes, and payment/order status.
    """

    __tablename__ = "sales"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    invoice_number = Column(String, unique=True, index=True)
    invoice_date = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    customer_id = Column(UUID(as_uuid=True), ForeignKey("customers.id"), nullable=True)
    user_id = Column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )  # Staff who made the sale
    client_id = Column(
        UUID(as_uuid=True), unique=True, nullable=True, index=True
    )  # For idempotency

    # Where it was sold: the Shop for counter sales. Empty on sales from before locations.
    location_id = Column(
        UUID(as_uuid=True), ForeignKey("plant_units.id"), nullable=True, index=True
    )
    order_type = Column(String, default="in-store")  # in-store, online
    sub_total = Column(Numeric(10, 2), default=0.0)
    tax_total = Column(Numeric(10, 2), default=0.0)
    discount = Column(Numeric(10, 2), default=0.0)
    total_amount = Column(Numeric(10, 2), default=0.0)

    payment_status = Column(String, default="pending")  # pending, paid, partial
    payment_method = Column(String, nullable=True)  # cash, upi, card, online
    order_status = Column(
        String, default="completed"
    )  # draft, confirmed, shipped, delivered, cancelled

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    items = relationship("SaleItem", back_populates="sale")
    customer = relationship("Customer")
    user = relationship("User")
    payments = relationship(
        "SalePayment",
        back_populates="sale",
        order_by="SalePayment.created_at",
        cascade="all, delete-orphan",
    )
    returns = relationship(
        "SaleReturn", back_populates="sale", order_by="SaleReturn.created_at.desc()"
    )

    # --- money, derived from the payment rows and the credit notes -------------

    @property
    def live_payments(self):
        return [p for p in self.payments if p.voided_at is None]

    @property
    def amount_paid(self):
        """Sum of un-voided payments. A bill from before payment rows existed has none:
        it counts as fully paid when it was marked paid, else nothing is known."""
        if not self.payments:
            return (
                Decimal(self.total_amount or 0)
                if self.payment_status == "paid"
                else Decimal("0.00")
            )
        return sum((Decimal(p.amount) for p in self.live_payments), Decimal("0.00"))

    @property
    def balance_due(self):
        return max(Decimal(self.total_amount or 0) - self.amount_paid, Decimal("0.00"))

    @property
    def returned_total(self):
        return sum((Decimal(r.refund_amount) for r in self.returns), Decimal("0.00"))

    @property
    def returned_quantities(self):
        out = {}
        for r in self.returns:
            for line in r.items:
                out[line.sale_item_id] = out.get(line.sale_item_id, 0) + line.quantity
        return out
