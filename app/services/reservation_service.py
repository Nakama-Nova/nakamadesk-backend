"""
Setting stock aside for an order while it is being paid for online, so two shoppers
cannot both pay for the last chair. See models/stock_reservation.py for the rules.

Made-to-order pieces are never reserved (nothing is on the shelf for them). Expired
reservations are simply ignored when counting, so nothing has to clean them up.
"""

from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.models.item import Item
from app.models.order import Order
from app.models.stock_reservation import StockReservation


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _active(now: datetime):
    return (
        StockReservation.released_at.is_(None),
        or_(StockReservation.expires_at.is_(None), StockReservation.expires_at > now),
    )


def reserved_qty(
    session: Session, item_id: UUID, exclude_order_id: Optional[UUID] = None
) -> int:
    """Units of an item currently set aside, not counting one order's own."""
    q = session.query(func.coalesce(func.sum(StockReservation.quantity), 0)).filter(
        StockReservation.item_id == item_id, *_active(_now())
    )
    if exclude_order_id is not None:
        q = q.filter(StockReservation.order_id != exclude_order_id)
    return int(q.scalar() or 0)


def available(
    session: Session, item: Item, exclude_order_id: Optional[UUID] = None
) -> int:
    """On the shelf minus what is set aside for others."""
    return int(item.current_stock or 0) - reserved_qty(
        session, item.id, exclude_order_id
    )


def reserve(session: Session, order: Order, minutes: int) -> Optional[datetime]:
    """
    Set aside the order's in-stock pieces for `minutes`, replacing any earlier hold of
    its own. Raises 400 if another shopper's holds leave too little. Returns when the
    hold ends, or None if nothing needed holding.
    """
    wanted = {}
    for line in order.items:
        if line.item_id and line.quantity:
            wanted[line.item_id] = wanted.get(line.item_id, 0) + line.quantity
    if not wanted:
        return None

    # Lock the items in a fixed order so two shoppers cannot deadlock each other.
    items = (
        session.query(Item)
        .filter(Item.id.in_(wanted))
        .order_by(Item.id)
        .with_for_update()
        .all()
    )
    expires = _now() + timedelta(minutes=minutes)
    held_any = False
    for item in items:
        if item.made_to_order:
            continue
        qty = wanted[item.id]
        if available(session, item, exclude_order_id=order.id) < qty:
            raise HTTPException(
                status_code=400, detail=f"Not enough stock for {item.name}"
            )
        row = (
            session.query(StockReservation)
            .filter(
                StockReservation.order_id == order.id,
                StockReservation.item_id == item.id,
            )
            .first()
        )
        if row is None:
            row = StockReservation(order_id=order.id, item_id=item.id, quantity=qty)
            session.add(row)
        row.quantity = qty
        row.released_at = None
        row.expires_at = expires
        held_any = True
    session.flush()
    return expires if held_any else None


def hold(session: Session, order_id: UUID) -> None:
    """Payment arrived: keep the units set aside until the order is billed or cancelled."""
    session.query(StockReservation).filter(
        StockReservation.order_id == order_id,
        StockReservation.released_at.is_(None),
    ).update({StockReservation.expires_at: None})


def release(session: Session, order_id: UUID) -> None:
    """The order was cancelled or billed (stock now leaves the shelf for real)."""
    session.query(StockReservation).filter(
        StockReservation.order_id == order_id,
        StockReservation.released_at.is_(None),
    ).update({StockReservation.released_at: _now()})
