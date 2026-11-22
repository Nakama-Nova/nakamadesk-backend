"""
Order emails: three short, transactional messages to the address a shopper typed at
website checkout (`Order.notify_email`, set only by `public_service.checkout`).

    order_received    right after the website checkout creates the order
    order_confirmed   when staff move an order draft -> confirmed
    payment_received  when staff record a payment (one email per payment)

No marketing, and orders staff create in the apps are never emailed: they have no
`notify_email`. A custom-order enquiry has no email field either.

How it runs. Callers hand `notify` the request's FastAPI `BackgroundTasks`; the send then
happens after the response is on its way (the API is a long-running uvicorn process, see
docs/DEPLOY_AWS.md), in its own database session. Nothing here ever raises into the
caller, so a slow or failing Brevo can never fail or slow the checkout, the status
change or the payment.

Once only. `send` first INSERTs an `order_email_log` row; its unique indexes let exactly
one of any number of racing requests claim an (order, kind) or (order, kind, payment).
The row is then updated to sent / failed. Nothing is retried. The per-address limit
(3 "order received" emails per address per 24 hours, across orders) is counted under a
per-address advisory lock so concurrent checkouts cannot slip past it.

Adding a kind later (in production, ready, delivered...): one template function like
`_payment_received` below that returns (subject, blocks), one entry in `_KINDS`, and one
`notify(...)` call where the thing happens.
"""

import html
import logging
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Callable, Dict, List, Optional, Tuple
from urllib.parse import quote

from fastapi import BackgroundTasks
from sqlalchemy import func, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.order import Order
from app.models.order_email_log import OrderEmailLog
from app.models.order_payment import OrderPayment
from app.services import brevo_client
from app.services.letter_pdf_service import money
from app.services.newsletter_service import STORE_NAME

logger = logging.getLogger(__name__)

CAP_PER_ADDRESS = 3  # "order received" emails to one address ...
CAP_WINDOW = timedelta(hours=24)  # ... in this long, across all orders

# A block is one piece of the email, rendered the same way into text and HTML:
#   ("p", text) paragraph   ("lines", [text...]) list   ("facts", [(label, value)...])
#   ("link", (label, url)) a button in HTML, label and URL in text
Block = Tuple[str, object]


def rupees(amount) -> str:
    """Rs. 1,04,500 (Indian grouping); paise only when there are some."""
    s = money(amount)
    return s[:-3] if s.endswith(".00") else s


def tracking_link(order_number: str) -> str:
    base = settings.STOREFRONT_URL or "http://localhost:3000"
    return f"{base}/track?order={quote(order_number, safe='')}"


# --- scheduling ----------------------------------------------------------------------


def notify(
    background: Optional[BackgroundTasks],
    order: Order,
    kind: str,
    payment_id: Optional[uuid.UUID] = None,
) -> None:
    """Queue one email to go out after the response. Never raises, never blocks."""
    try:
        if background is None or not order.notify_email:
            return
        background.add_task(send, order.id, kind, payment_id)
    except Exception:
        logger.exception("could not queue %s email", kind)


def send(
    order_id: uuid.UUID, kind: str, payment_id: Optional[uuid.UUID] = None
) -> None:
    """The background job. Opens its own session; swallows (and logs) everything."""
    try:
        with SessionLocal() as db:
            _send(db, order_id, kind, payment_id)
    except Exception:
        logger.exception("order email %s for order %s crashed", kind, order_id)


# --- the send ------------------------------------------------------------------------


def _log_only() -> bool:
    """Local development without Brevo: log the link instead."""
    return settings.ENV == "dev" and not settings.IS_PROD


def _skip_reason(db: Session, kind: str, payment_id) -> Optional[str]:
    if not settings.ORDER_EMAILS_ENABLED:
        return "switched off (ORDER_EMAILS_ENABLED)"
    if not (
        settings.BREVO_API_KEY
        and settings.BREVO_SENDER_EMAIL
        and settings.STOREFRONT_URL
    ):
        return "Brevo or STOREFRONT_URL not set up"
    if kind == "payment_received":
        p = db.get(OrderPayment, payment_id)
        if p is None or p.voided_at is not None:
            return "payment was voided"
    return None


def _claim(
    db: Session, order: Order, kind: str, payment_id, reason: Optional[str]
) -> Optional[OrderEmailLog]:
    """Insert the log row that is also the lock. None = someone else already has it."""
    to = order.notify_email
    try:
        if kind == "order_received":
            # one checkout at a time per address, so the count below cannot be raced
            db.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:k))"),
                {"k": f"order-email:{to}"},
            )
            if reason is None:
                recent = (
                    db.query(func.count(OrderEmailLog.id))
                    .filter(
                        OrderEmailLog.to_email == to,
                        OrderEmailLog.kind == kind,
                        OrderEmailLog.status != "skipped",
                        OrderEmailLog.created_at
                        > datetime.now(timezone.utc) - CAP_WINDOW,
                    )
                    .scalar()
                )
                if recent >= CAP_PER_ADDRESS:
                    reason = f"limit of {CAP_PER_ADDRESS} per address per 24 hours"
        row = OrderEmailLog(
            order_id=order.id,
            kind=kind,
            payment_id=payment_id,
            to_email=to,
            status="skipped" if reason else "sending",
            error=reason,
        )
        db.add(row)
        db.commit()
        return row
    except IntegrityError:  # the same email was claimed a moment ago (or earlier)
        db.rollback()
        return None


def _send(db: Session, order_id, kind: str, payment_id) -> None:
    order = (
        db.query(Order)
        .options(joinedload(Order.items), joinedload(Order.payments))
        .filter(Order.id == order_id)
        .first()
    )
    if order is None or not order.notify_email or kind not in _KINDS:
        return
    row = _claim(db, order, kind, payment_id, _skip_reason(db, kind, payment_id))
    if row is None:
        return
    if row.status == "skipped":
        logger.info(
            "order email %s for %s skipped: %s", kind, order.order_number, row.error
        )
        if _log_only():
            logger.warning(
                "ORDER EMAIL %s not sent (dev): %s",
                kind,
                tracking_link(order.order_number),
            )
        return
    try:
        subject, blocks = _KINDS[kind](order, payment_id, db)
        brevo_client.send_email(
            order.notify_email, subject, _as_text(blocks), _as_html(blocks)
        )
        row.status, row.error = "sent", None
    except brevo_client.BrevoError as e:
        row.status, row.error = "failed", str(e)[:200]
        logger.error("order email %s for %s failed: %s", kind, order.order_number, e)
    except Exception as e:  # a bug in a template must not leave the row "sending"
        row.status, row.error = "failed", type(e).__name__
        logger.exception("order email %s for %s crashed", kind, order.order_number)
    db.commit()


# --- the three emails ----------------------------------------------------------------


def _amounts(order: Order):
    # The numbers the tracking page shows (public_service.to_public_order).
    from app.services import public_service  # late: it imports order_service

    return public_service.to_public_order(order)


def _footer(order: Order) -> List[Block]:
    contact = (
        f"write to us at {settings.EMAIL_REPLY_TO}"
        if settings.EMAIL_REPLY_TO
        else "let the shop know"
    )
    return [
        (
            "p",
            "You can follow your order any time on our tracking page. It will ask for "
            "the phone number you ordered with.",
        ),
        ("link", ("Track my order", tracking_link(order.order_number))),
        (
            "p",
            f"If you did not place this order, you can ignore this email, or {contact}.",
        ),
        ("p", f"Warmly,\n{STORE_NAME}"),
    ]


def _order_received(order: Order, payment_id, db: Session):
    po = _amounts(order)
    return (
        f"We received your order {order.order_number} - {STORE_NAME}",
        [
            ("p", "Hello,"),
            (
                "p",
                f"Thank you for your order {order.order_number}. We have received it. "
                "Nothing has been charged.",
            ),
            ("lines", [f"{i.name} x {i.quantity}" for i in po.items]),
            ("facts", [("Total", rupees(po.total))]),
            (
                "p",
                "We will call or WhatsApp you soon to confirm the price and the "
                "delivery date.",
            ),
        ]
        + _footer(order),
    )


def _order_confirmed(order: Order, payment_id, db: Session):
    from app.services import public_service

    po = _amounts(order)
    blocks: List[Block] = [
        ("p", "Hello,"),
        ("p", f"Your order {order.order_number} is confirmed. Thank you!"),
        (
            "facts",
            [
                ("Total", rupees(po.total)),
                ("Advance paid so far", rupees(po.advance_paid)),
                ("Balance due", rupees(po.balance_due)),
            ],
        ),
    ]
    # the same rule as the tracking page's "Pay by UPI" button
    if order.status in public_service.PAY_STATUSES and po.balance_due > 0:
        info = public_service.payment_info()
        if info.upi_id:
            blocks.append(
                (
                    "p",
                    f"To pay by UPI, send the amount to the UPI id {info.upi_id} "
                    f"(shown as {info.payee_name}) and write your order number "
                    f"{order.order_number} in the note. The tracking page below also "
                    "has a Pay by UPI button. You can pay in cash at the shop too.",
                )
            )
        else:
            blocks.append(("p", "You can pay in cash at the shop."))
    return (
        f"Your order {order.order_number} is confirmed - {STORE_NAME}",
        blocks + _footer(order),
    )


def _payment_received(order: Order, payment_id, db: Session):
    po = _amounts(order)
    paid = db.get(OrderPayment, payment_id)
    due = Decimal(po.balance_due)
    return (
        f"Payment received for order {order.order_number} - {STORE_NAME}",
        [
            ("p", "Hello,"),
            (
                "p",
                f"We received {rupees(paid.amount)} for your order "
                f"{order.order_number}. Thank you!",
            ),
            (
                "p",
                (
                    f"The balance still due is {rupees(due)}."
                    if due > 0
                    else "Nothing more is due on this order."
                ),
            ),
        ]
        + _footer(order),
    )


_KINDS: Dict[str, Callable] = {
    "order_received": _order_received,
    "order_confirmed": _order_confirmed,
    "payment_received": _payment_received,
}


# --- rendering (everything is escaped in _as_html) -------------------------------------


def _as_text(blocks: List[Block]) -> str:
    out = []
    for kind, v in blocks:
        if kind == "p":
            out.append(v)
        elif kind == "lines":
            out.append("\n".join(f"- {line}" for line in v))
        elif kind == "facts":
            out.append("\n".join(f"{k}: {val}" for k, val in v))
        elif kind == "link":
            out.append(f"{v[0]}: {v[1]}")
    return "\n\n".join(out) + "\n"


def _as_html(blocks: List[Block]) -> str:
    e = html.escape
    out = []
    for kind, v in blocks:
        if kind == "p":
            out.append("<p>" + e(v).replace("\n", "<br>") + "</p>")
        elif kind == "lines":
            out.append("<ul>" + "".join(f"<li>{e(line)}</li>" for line in v) + "</ul>")
        elif kind == "facts":
            out.append(
                '<table style="border-collapse:collapse">'
                + "".join(
                    f'<tr><td style="padding:2px 16px 2px 0;color:#7a6a5a">{e(k)}</td>'
                    f"<td><b>{e(val)}</b></td></tr>"
                    for k, val in v
                )
                + "</table>"
            )
        elif kind == "link":
            out.append(
                f'<p><a href="{e(v[1])}" style="display:inline-block;padding:12px 22px;'
                "background:#5a3a1e;color:#ffffff;text-decoration:none;"
                f'border-radius:4px">{e(v[0])}</a></p>'
            )
    return (
        '<div style="font-family:Georgia,serif;font-size:16px;line-height:1.6;'
        'color:#2b2118;max-width:520px">' + "".join(out) + "</div>"
    )
