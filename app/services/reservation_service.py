"""
Setting stock aside for an order the workshop has promised, so the same unit is not
promised twice. When staff confirm an order (after talking to the customer), its in-stock
pieces are held until the order is billed, delivered or cancelled.

Stock itself (items.current_stock) does not move until the order is billed. A hold only
lowers what can be sold to someone else: online checkout, counter sales and other
orders' holds all see "on the shelf minus held". Made-to-order pieces are never held
(nothing is on the shelf for them).
"""

from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.item import Item
from app.models.order import Order
from app.models.stock_reservation import StockReservation


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def reserved_qty(
    session: Session, item_id: UUID, exclude_order_id: Optional[UUID] = None
) -> int:
    """Units of an item currently held for orders, not counting one order's own."""
    q = session.query(func.coalesce(func.sum(StockReservation.quantity), 0)).filter(
        StockReservation.item_id == item_id,
        StockReservation.released_at.is_(None),
    )
    if exclude_order_id is not None:
        q = q.filter(StockReservation.order_id != exclude_order_id)
    return int(q.scalar() or 0)


def held_by_item(session: Session) -> dict:
    """Units held for orders right now, per item: {item_id: units}. Items with no hold are left out."""
    rows = (
        session.query(StockReservation.item_id, func.sum(StockReservation.quantity))
        .filter(StockReservation.released_at.is_(None))
        .group_by(StockReservation.item_id)
        .all()
    )
    return {str(item_id): int(total) for item_id, total in rows if total}


def available(
    session: Session, item: Item, exclude_order_id: Optional[UUID] = None
) -> int:
    """On the shelf minus what is held for other orders."""
    return int(item.current_stock or 0) - reserved_qty(
        session, item.id, exclude_order_id
    )


def set_aside(session: Session, order: Order) -> None:
    """
    Hold the order's in-stock pieces. If the shelf has fewer than the order needs, hold
    what is there rather than refuse: staff may be about to make or buy the rest, and
    confirming an order must not be blocked by this. Replaces any earlier hold of its own.
    """
    wanted = {}
    for line in order.items:
        if line.item_id and line.quantity:
            wanted[line.item_id] = wanted.get(line.item_id, 0) + line.quantity
    if not wanted:
        return
    # Lock the items in a fixed order so two confirmations cannot deadlock each other.
    items = (
        session.query(Item)
        .filter(Item.id.in_(wanted))
        .order_by(Item.id)
        .with_for_update()
        .all()
    )
    for item in items:
        if item.made_to_order:
            continue
        hold = min(wanted[item.id], max(0, available(session, item, order.id)))
        row = (
            session.query(StockReservation)
            .filter(
                StockReservation.order_id == order.id,
                StockReservation.item_id == item.id,
            )
            .first()
        )
        if hold == 0:
            if row is not None:
                row.released_at = _now()
            continue
        if row is None:
            row = StockReservation(order_id=order.id, item_id=item.id, quantity=hold)
            session.add(row)
        row.quantity = hold
        row.released_at = None
    session.flush()


def release(session: Session, order_id: UUID) -> None:
    """The order was cancelled, delivered or billed (stock now leaves the shelf for real)."""
    session.query(StockReservation).filter(
        StockReservation.order_id == order_id,
        StockReservation.released_at.is_(None),
    ).update({StockReservation.released_at: _now()})
