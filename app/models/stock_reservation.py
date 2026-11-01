import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Integer,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID

from app.db.base import Base


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class StockReservation(Base):
    """
    Units of a catalogue item set aside for an order that is being paid for online.

    Stock itself (items.current_stock) is not touched: it only goes down when the order
    is billed. A reservation just stops the same units being promised twice. It counts
    while released_at is empty and expires_at is empty (paid, held until billed or
    cancelled) or still in the future (waiting for the payment).
    """

    __tablename__ = "stock_reservations"
    __table_args__ = (
        UniqueConstraint(
            "order_id", "item_id", name="uq_stock_reservations_order_item"
        ),
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
        index=True,
    )
    item_id = Column(
        UUID(as_uuid=True), ForeignKey("items.id"), nullable=False, index=True
    )
    quantity = Column(Integer, nullable=False)
    expires_at = Column(DateTime, nullable=True)
    released_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=_now)
