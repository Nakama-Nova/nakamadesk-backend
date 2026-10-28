"""
What staff should be told about, worked out from the shop's own records.

There is no notifications table and no push service: the phone app and the desktop ask
"what happened since <time>?" every minute or so and show what is new. Each item has a
stable id (order:<uuid>), so a client that asks twice never shows the same thing twice.

Who is told what:
  owner, manager  new website orders, money received on an order, stock running low,
                  finished or failed 3D scans (a manager not for restoration scans)
  sales           new website orders
  artisan         finished or failed 3D scans (their own work)
Everyone else gets nothing.
"""

from datetime import datetime, timedelta, timezone
from typing import Dict, List

from sqlalchemy.orm import Session

from app.models.enums import UserRole
from app.models.inventory_movement import InventoryMovement
from app.models.item import Item
from app.models.order import Order
from app.models.order_payment import OrderPayment
from app.models.restoration import RestorationProject
from app.models.scan_job import ScanJob
from app.models.user import User

MAX_LOOKBACK = timedelta(days=3)  # a phone switched off for a week is not flooded
MONEY_ROLES = (UserRole.OWNER, UserRole.MANAGER)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def clamp_since(since: datetime) -> datetime:
    """A usable start time: in UTC, not in the future, not more than MAX_LOOKBACK ago."""
    if since.tzinfo is None:
        since = since.replace(tzinfo=timezone.utc)
    since = since.astimezone(timezone.utc)
    now = now_utc()
    return max(min(since, now), now - MAX_LOOKBACK)


def _naive(dt: datetime) -> datetime:
    """For columns stored without a time zone (they hold UTC)."""
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def _iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def _rupees(value) -> str:
    return f"Rs. {float(value or 0):,.2f}"


def _how(method) -> str:
    return "UPI" if method == "upi" else (method or "cash").title()


def _event(id_, kind, title, body, at, screen, target_id=None) -> Dict:
    return {
        "id": id_,
        "kind": kind,
        "title": title,
        "body": body,
        "created_at": _iso(at),
        "screen": screen,  # which screen the app opens when it is tapped
        "target_id": str(target_id) if target_id else None,
    }


def feed(db: Session, user: User, since: datetime, limit: int = 50) -> List[Dict]:
    """Everything this user should hear about after `since`, oldest first."""
    since = clamp_since(since)
    role = user.role
    events: List[Dict] = []

    if role in MONEY_ROLES + (UserRole.SALES,):
        for o in (
            db.query(Order)
            .filter(Order.source == "web", Order.created_at > _naive(since))
            .order_by(Order.created_at)
            .limit(limit)
            .all()
        ):
            who = o.customer_name or "a customer"
            events.append(
                _event(
                    f"order:{o.id}",
                    "order_web",
                    f"New website order {o.order_number}",
                    f"{who}, {_rupees(o.estimated_amount)}",
                    o.created_at,
                    "orders",
                    o.id,
                )
            )

    if role in MONEY_ROLES:
        payments = (
            db.query(OrderPayment, Order)
            .join(Order, Order.id == OrderPayment.order_id)
            .filter(OrderPayment.created_at > since)
            .order_by(OrderPayment.created_at)
            .limit(limit)
            .all()
        )
        for p, o in payments:
            events.append(
                _event(
                    f"payment:{p.id}",
                    "payment",
                    f"{_rupees(p.amount)} received on {o.order_number}",
                    _how(p.method) + (f", {p.reference}" if p.reference else ""),
                    p.created_at,
                    "orders",
                    o.id,
                )
            )

        # stock that has just dropped to its warning level (one notice per product)
        seen = set()
        low = (
            db.query(InventoryMovement, Item)
            .join(Item, Item.id == InventoryMovement.item_id)
            .filter(
                InventoryMovement.created_at > _naive(since),
                InventoryMovement.quantity < 0,
                Item.is_active.is_(True),
                Item.current_stock <= Item.min_stock,
            )
            .order_by(InventoryMovement.created_at.desc())
            .limit(limit * 2)
            .all()
        )
        for m, item in low:
            if item.id in seen:
                continue
            seen.add(item.id)
            left = item.current_stock or 0
            events.append(
                _event(
                    f"lowstock:{item.id}:{m.id}",
                    "low_stock",
                    f"Low stock: {item.name}",
                    "Out of stock" if left <= 0 else f"Only {left} left",
                    m.created_at,
                    "stock",
                    item.id,
                )
            )

    if role in MONEY_ROLES or role == UserRole.ARTISAN:
        scans = (
            db.query(ScanJob)
            .filter(
                ScanJob.finished_at.isnot(None),
                ScanJob.finished_at > since,
                ScanJob.status.in_(("done", "failed")),
            )
            .order_by(ScanJob.finished_at)
            .limit(limit)
            .all()
        )
        for s in scans:
            if s.artisan_only and role == UserRole.MANAGER:
                continue  # restoration and sculpture scans are the artisans' and the owner's
            name = _scan_name(db, s)
            ok = s.status == "done"
            events.append(
                _event(
                    f"scan:{s.id}:{s.status}",
                    "scan_done" if ok else "scan_failed",
                    f"3D model ready: {name}" if ok else f"3D scan failed: {name}",
                    (
                        "Open it to see the files"
                        if ok
                        else (s.error or "See the scan for why")[:140]
                    ),
                    s.finished_at,
                    "restoration" if s.restoration_id else "stock",
                    s.restoration_id or s.item_id,
                )
            )

    events.sort(key=lambda e: e["created_at"])
    return events[-limit:]


def _scan_name(db: Session, scan: ScanJob) -> str:
    if scan.restoration_id:
        p = db.get(RestorationProject, scan.restoration_id)
        if p:
            return p.title
    if scan.item_id:
        item = db.get(Item, scan.item_id)
        if item:
            return item.name
    return "a scan"
