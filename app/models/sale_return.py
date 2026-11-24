import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.db.base import Base


class SaleReturn(Base):
    """
    One credit note: goods the customer gave back from a bill, and the refund.
    The bill is never edited; reports subtract the credit note in its own month.
    """

    __tablename__ = "sale_returns"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    credit_note_number = Column(String, unique=True, index=True, nullable=False)
    sale_id = Column(
        UUID(as_uuid=True), ForeignKey("sales.id"), nullable=False, index=True
    )
    reason = Column(String, nullable=False)
    refund_method = Column(String, nullable=False)
    refund_amount = Column(Numeric(12, 2), nullable=False)
    restocked = Column(Boolean, nullable=False, default=True)
    created_by = Column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=text("now()"),
    )

    sale = relationship("Sale", back_populates="returns")
    items = relationship(
        "SaleReturnItem", back_populates="sale_return", cascade="all, delete-orphan"
    )

    @property
    def invoice_number(self):
        return self.sale.invoice_number if self.sale else None


class SaleReturnItem(Base):
    __tablename__ = "sale_return_items"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    return_id = Column(
        UUID(as_uuid=True),
        ForeignKey("sale_returns.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    sale_item_id = Column(
        UUID(as_uuid=True), ForeignKey("sale_items.id"), nullable=False, index=True
    )
    item_id = Column(UUID(as_uuid=True), ForeignKey("items.id"), nullable=True)
    quantity = Column(Integer, nullable=False)
    taxable_value = Column(Numeric(12, 2), nullable=False)
    cgst_amount = Column(Numeric(12, 2), nullable=False)
    sgst_amount = Column(Numeric(12, 2), nullable=False)
    amount = Column(Numeric(12, 2), nullable=False)  # what the customer gets back

    sale_return = relationship("SaleReturn", back_populates="items")
    sale_item = relationship("SaleItem")

    @property
    def item_name(self):
        return self.sale_item.item_name if self.sale_item else None
