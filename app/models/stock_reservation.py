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
    Units of a catalogue item held for a confirmed order until it is billed, delivered
    or cancelled (then released_at is set). Stock itself (items.current_stock) is not
    touched: a hold only stops the same units being promised twice.
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
    released_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=_now)
