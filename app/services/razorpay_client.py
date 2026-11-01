"""
The only place that talks to Razorpay. Set RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET
(test keys start with rzp_test_, live ones with rzp_live_) and RAZORPAY_WEBHOOK_SECRET
(the secret you type into Razorpay's Webhooks page). Without them every call here
answers 503 "not set up yet" instead of failing in some other way.

Amounts to Razorpay are in paise (rupees x 100) as whole numbers.
"""

import hashlib
import hmac
import logging

import httpx
from fastapi import HTTPException

from app.core.config import settings

logger = logging.getLogger(__name__)

API = "https://api.razorpay.com/v1"
NOT_SET_UP = "Online payment is not set up yet"


def configured() -> bool:
    return bool(settings.RAZORPAY_KEY_ID and settings.RAZORPAY_KEY_SECRET)


def mode() -> str:
    return "test" if settings.RAZORPAY_KEY_ID.startswith("rzp_test_") else "live"


def create_order(amount_paise: int, receipt: str, notes: dict) -> dict:
    """Make a Razorpay payment order. Returns its JSON (the `id` is what Checkout opens)."""
    if not configured():
        raise HTTPException(status_code=503, detail=NOT_SET_UP)
    try:
        r = httpx.post(
            f"{API}/orders",
            auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET),
            json={
                "amount": amount_paise,
                "currency": "INR",
                "receipt": receipt[:40],
                "notes": notes,
            },
            timeout=10,
        )
        r.raise_for_status()
        return r.json()
    except (httpx.HTTPError, ValueError):
        logger.exception("Razorpay order could not be created")
        raise HTTPException(
            status_code=502, detail="Could not reach the payment service, try again"
        ) from None


def _same(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())


def verify_payment_signature(order_id: str, payment_id: str, signature: str) -> bool:
    """The signature Checkout returned: HMAC-SHA256 of 'order_id|payment_id' with the key secret."""
    if not configured():
        raise HTTPException(status_code=503, detail=NOT_SET_UP)
    expected = hmac.new(
        settings.RAZORPAY_KEY_SECRET.encode(),
        f"{order_id}|{payment_id}".encode(),
        hashlib.sha256,
    ).hexdigest()
    return _same(expected, signature or "")


def verify_webhook(raw_body: bytes, signature: str) -> bool:
    """X-Razorpay-Signature: HMAC-SHA256 of the raw request body with the webhook secret."""
    if not settings.RAZORPAY_WEBHOOK_SECRET:
        raise HTTPException(status_code=503, detail=NOT_SET_UP)
    expected = hmac.new(
        settings.RAZORPAY_WEBHOOK_SECRET.encode(), raw_body, hashlib.sha256
    ).hexdigest()
    return _same(expected, signature or "")
