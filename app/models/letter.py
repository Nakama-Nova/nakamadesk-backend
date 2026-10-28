import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Column,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.db.base import Base

# kind -> (number prefix, what it is called on the page)
KINDS = {
    "quotation": ("QTN", "Quotation"),
    "order_confirmation": ("OCF", "Order confirmation and advance receipt"),
    "purchase_receipt": ("PRC", "Purchase receipt"),
    "completion_certificate": ("CCT", "Certificate of completion"),
}


class Letter(Base):
    """
    An official letter the shop issues: a quotation, an order confirmation with its advance
    receipt, a receipt for a piece bought from another artisan, or a certificate that a temple
    restoration is complete. Each has a number (QTN-2026-27-0001, numbered per financial year) issued once and never reused.
    The page is drawn from `payload`, a snapshot taken when it was issued, so a letter always
    reads the same however the order or restoration changes later. A wrong one is voided
    (with a reason) and stays on record.
    """

    __tablename__ = "letters"
    __table_args__ = (Index("ix_letters_kind_issued", "kind", "issued_on"),)

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    number = Column(String, unique=True, index=True, nullable=False)
    kind = Column(String, nullable=False)
    status = Column(String, nullable=False, default="issued", server_default="issued")
    issued_on = Column(Date, nullable=False)
    subject = Column(String, nullable=False)
    party_name = Column(String, nullable=False)
    amount = Column(Numeric(12, 2), nullable=True)
    order_id = Column(
        UUID(as_uuid=True), ForeignKey("orders.id", ondelete="SET NULL"), nullable=True
    )
    item_id = Column(
        UUID(as_uuid=True), ForeignKey("items.id", ondelete="SET NULL"), nullable=True
    )
    restoration_id = Column(
        UUID(as_uuid=True),
        ForeignKey("restoration_projects.id", ondelete="SET NULL"),
        nullable=True,
    )
    supplier_id = Column(
        UUID(as_uuid=True),
        ForeignKey("suppliers.id", ondelete="SET NULL"),
        nullable=True,
    )
    payload = Column(JSONB, nullable=False)
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


class BusinessProfile(Base):
    """The letterhead: one row (id = 1) the owner can edit. Defaults apply until it is saved."""

    __tablename__ = "business_profile"

    id = Column(Integer, primary_key=True, default=1)
    name = Column(String, nullable=False)
    tagline = Column(String, nullable=True)
    address = Column(Text, nullable=True)
    phone = Column(String, nullable=True)
    email = Column(String, nullable=True)
    gstin = Column(String, nullable=True)  # printed only once the shop is registered
    signatory_name = Column(String, nullable=True)
    signatory_title = Column(String, nullable=True)
    footer_note = Column(Text, nullable=True)
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        server_default=text("now()"),
    )
