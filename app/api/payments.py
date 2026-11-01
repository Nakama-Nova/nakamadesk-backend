"""Razorpay's webhook. Public (Razorpay calls it), protected by its signature."""

import json

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.db.deps import get_db
from app.services import payment_service, razorpay_client

router = APIRouter(prefix="/payments", tags=["Payments"])


def _handle(db: Session, event: dict) -> str:
    name = event.get("event")
    entity = ((event.get("payload") or {}).get("payment") or {}).get("entity") or {}
    order_id, payment_id = entity.get("order_id"), entity.get("id")
    if not order_id or not payment_id:
        return "ignored"  # not a payment for one of our orders
    if name == "payment.captured":
        return payment_service.record_captured(
            db, order_id, payment_id, entity.get("amount")
        )
    if name == "payment.failed":
        payment_service.mark_failed(db, order_id, payment_id)
        return "failed"
    return "ignored"


@router.post("/razorpay/webhook")
async def razorpay_webhook(request: Request, db: Session = Depends(get_db)):
    """
    Set this URL in Razorpay (Settings, Webhooks) with the events payment.captured and
    payment.failed, and the same secret as RAZORPAY_WEBHOOK_SECRET. The signature is
    checked against the raw body before anything is read. Always answers 200 for a
    valid call, even for an event we ignore or have already seen, so Razorpay stops
    retrying it.
    """
    raw = await request.body()
    if not razorpay_client.verify_webhook(
        raw, request.headers.get("x-razorpay-signature", "")
    ):
        raise HTTPException(status_code=400, detail="Bad signature")
    try:
        event = json.loads(raw)
    except ValueError:
        raise HTTPException(status_code=400, detail="Not JSON") from None
    if not isinstance(event, dict):
        raise HTTPException(status_code=400, detail="Not an event")
    result = await run_in_threadpool(_handle, db, event)
    return {"status": result}
