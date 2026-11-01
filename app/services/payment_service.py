"""
Paying an order online with Razorpay.

  start()          make a Razorpay payment order for (part of) what is due, and set the
                   order's in-stock pieces aside while the shopper pays
  record_captured() money arrived: write it on the order, confirm the order, keep the
                   stock set aside until billing. Called from both the shopper's
                   browser (after Checkout) and Razorpay's webhook, so it is safe to
                   run twice for the same payment.
  mark_failed()    an attempt failed; the shopper may try again

The webhook is the source of truth. The browser's confirmation only makes the page
show "paid" without waiting for it. Nothing is credited unless Razorpay's signature
checks out, and only for the amount the payment order was made for.
"""

import logging
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.order import Order
from app.models.order_payment import OrderPayment
from app.models.payment_intent import PaymentIntent
from app.models.sale import Sale
from app.schemas.payment import PaymentStart
from app.services import razorpay_client, reservation_service
from app.services.order_invoice_service import agreed_total, payment_status

logger = logging.getLogger(__name__)

CENT = Decimal("0.01")
MIN_PAYMENT = Decimal("1.00")


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def balance_due(order: Order) -> Decimal:
    return max(
        Decimal("0.00"), agreed_total(order) - Decimal(order.advance_paid or 0)
    ).quantize(CENT)


def _start_response(
    order: Order, intent: PaymentIntent, expires: Optional[datetime]
) -> PaymentStart:
    customer = order.customer
    return PaymentStart(
        order_number=order.order_number,
        key_id=settings.RAZORPAY_KEY_ID,
        mode=razorpay_client.mode(),
        razorpay_order_id=intent.provider_order_id,
        amount_paise=int(intent.amount * 100),
        currency=intent.currency,
        hold_expires_at=expires,
        name=customer.name if customer else None,
        email=customer.email if customer else None,
        contact=customer.phone if customer else None,
    )


def start(
    session: Session, order_id: UUID, amount: Optional[Decimal] = None
) -> PaymentStart:
    """
    Begin an online payment. `amount` defaults to the whole balance due; it may be
    less (an advance) but never more. Asking again for the same amount while a
    payment is open reuses it and extends the hold.
    """
    # Fail early and clearly if Razorpay is not set up, before touching any stock.
    if not razorpay_client.configured():
        raise HTTPException(status_code=503, detail=razorpay_client.NOT_SET_UP)
    minutes = settings.RESERVATION_MINUTES
    try:
        order = (
            session.query(Order).filter(Order.id == order_id).with_for_update().first()
        )
        if order is None:
            raise HTTPException(status_code=404, detail="Order not found")
        if order.status == "cancelled":
            raise HTTPException(
                status_code=409, detail="A cancelled order cannot take payments"
            )
        due = balance_due(order)
        if due <= 0:
            raise HTTPException(status_code=400, detail="Nothing is due on this order")
        pay = due if amount is None else Decimal(amount).quantize(CENT)
        if pay < MIN_PAYMENT:
            raise HTTPException(
                status_code=400, detail="The smallest online payment is Rs 1"
            )
        if pay > due:
            raise HTTPException(
                status_code=400, detail=f"That is more than the balance due (Rs {due})"
            )

        expires = reservation_service.reserve(session, order, minutes)
        open_intent = (
            session.query(PaymentIntent)
            .filter(
                PaymentIntent.order_id == order.id,
                PaymentIntent.status == "created",
                PaymentIntent.amount == pay,
                PaymentIntent.created_at > _now() - timedelta(minutes=minutes),
            )
            .order_by(PaymentIntent.created_at.desc())
            .first()
        )
        if open_intent is None:
            # ponytail: the Razorpay call happens while the order row is locked. It is
            # one short call at this volume; move it out if checkouts ever queue up.
            rzp = razorpay_client.create_order(
                int(pay * 100),
                receipt=order.order_number,
                notes={"order_number": order.order_number},
            )
            open_intent = PaymentIntent(
                order_id=order.id, provider_order_id=rzp["id"], amount=pay
            )
            session.add(open_intent)
        session.commit()
        return _start_response(order, open_intent, expires)
    except Exception:
        session.rollback()
        raise


def record_captured(
    session: Session,
    provider_order_id: str,
    payment_id: str,
    amount_paise: Optional[int] = None,
) -> str:
    """
    Credit a captured payment to its order. Returns what happened: "credited",
    "duplicate" (already recorded), "unknown" (not one of our payment orders) or
    "mismatch" (paid a different amount than asked for: nothing is credited).
    `amount_paise` is checked when Razorpay tells us (the webhook).
    """
    intent = (
        session.query(PaymentIntent)
        .filter(PaymentIntent.provider_order_id == provider_order_id)
        .with_for_update()
        .first()
    )
    if intent is None:
        session.commit()  # nothing to write; let go of the lock
        return "unknown"
    if amount_paise is not None and amount_paise != int(intent.amount * 100):
        intent.status = "mismatch"
        intent.provider_payment_id = payment_id
        session.commit()
        logger.error(
            "Razorpay payment %s was %s paise but order %s asked for %s: not credited",
            payment_id,
            amount_paise,
            provider_order_id,
            int(intent.amount * 100),
        )
        return "mismatch"
    already = (
        session.query(OrderPayment)
        .filter(OrderPayment.method == "razorpay", OrderPayment.reference == payment_id)
        .first()
    )
    if already is not None:
        session.commit()  # already recorded; let go of the lock
        return "duplicate"

    order = (
        session.query(Order)
        .filter(Order.id == intent.order_id)
        .populate_existing()  # read the row as it is now, not as this session saw it
        .with_for_update()
        .first()
    )
    amount = Decimal(intent.amount)
    session.add(
        OrderPayment(
            order_id=order.id,
            amount=amount,
            method="razorpay",
            reference=payment_id,
            note=f"Razorpay ({razorpay_client.mode()} mode)",
            paid_on=date.today(),
            created_by=None,
        )
    )
    order.advance_paid = Decimal(order.advance_paid or 0) + amount
    if order.sale_id:
        sale = session.get(Sale, order.sale_id)
        sale.payment_status = payment_status(order.advance_paid, sale.total_amount)
        sale.payment_method = "razorpay"
    intent.status = "paid"
    intent.provider_payment_id = payment_id
    intent.paid_at = _now()

    if order.status == "cancelled":
        # The money did arrive, so it is recorded; a person has to refund it.
        reservation_service.release(session, order.id)
        logger.warning(
            "Razorpay payment %s arrived for cancelled order %s: refund it",
            payment_id,
            order.order_number,
        )
    else:
        if order.status == "draft":
            order.status = "confirmed"
        reservation_service.hold(session, order.id)
    session.commit()
    return "credited"


def mark_failed(session: Session, provider_order_id: str, payment_id: str) -> None:
    intent = (
        session.query(PaymentIntent)
        .filter(PaymentIntent.provider_order_id == provider_order_id)
        .with_for_update()
        .first()
    )
    if intent is not None and intent.status == "created":
        intent.status = "failed"
        intent.provider_payment_id = payment_id
    session.commit()  # also lets go of the lock when there was nothing to change


def verify_from_browser(
    session: Session,
    order: Order,
    razorpay_order_id: str,
    payment_id: str,
    signature: str,
) -> Order:
    """
    The shopper's browser says the payment went through. Trust it only if Razorpay's
    signature for this exact order and payment id checks out, and only for a payment
    order that was made for THIS order.
    """
    intent = (
        session.query(PaymentIntent)
        .filter(
            PaymentIntent.provider_order_id == razorpay_order_id,
            PaymentIntent.order_id == order.id,
        )
        .first()
    )
    if intent is None:
        raise HTTPException(status_code=404, detail="Payment not found")
    if not razorpay_client.verify_payment_signature(
        razorpay_order_id, payment_id, signature
    ):
        raise HTTPException(status_code=400, detail="Payment could not be verified")
    record_captured(session, razorpay_order_id, payment_id)
    session.refresh(order)
    return order
