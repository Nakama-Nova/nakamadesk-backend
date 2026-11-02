"""
Prove that a signed-in shopper's phone number is theirs, with a code sent by SMS.

Same rules as the emailed sign-in code: only a keyed hash is stored, a code lives
CODE_MINUTES, allows MAX_ATTEMPTS wrong tries, works once, and a new code replaces the old.

Every text costs money and can be abused ("SMS pumping"), so asking for a code is
limited four ways: per IP (on the route), MAX_CODES_PER_HOUR per account,
MAX_CODES_PER_PHONE_PER_DAY per number whoever asks, and SMS_DAILY_LIMIT in total. Only numbers starting with one of
SMS_ALLOWED_PREFIXES (+91 by default) get a code at all.
"""

import hashlib
import hmac
import logging
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core import security
from app.core.config import settings
from app.models.customer_account import CustomerAccount, PhoneCode
from app.services import sms_service

logger = logging.getLogger(__name__)

CODE_MINUTES = 10
MAX_ATTEMPTS = 5
MAX_CODES_PER_HOUR = 3  # per account
MAX_CODES_PER_PHONE_PER_DAY = 5  # per number, across all accounts
BAD_CODE = "That code is wrong or has expired. Ask for a new one."
BAD_PHONE = "Enter a valid mobile number, for example 98765 43210."


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _hash(account: CustomerAccount, phone: str, code: str) -> str:
    key = security.SECRET_KEY.encode()
    return hmac.new(
        key, f"{account.id}:{phone}:{code}".encode(), hashlib.sha256
    ).hexdigest()


def _normalize(raw: str) -> str:
    try:
        return sms_service.normalize_phone(raw)
    except ValueError:
        raise HTTPException(status_code=422, detail=BAD_PHONE) from None


def request_code(db: Session, account: CustomerAccount, raw_phone: str) -> None:
    """Text a fresh code to the number. 429 when too many were asked for."""
    phone = _normalize(raw_phone)
    now = _now()
    db.query(PhoneCode).filter(PhoneCode.created_at < now - timedelta(days=1)).delete()

    per_account = (
        db.query(func.count(PhoneCode.id))
        .filter(
            PhoneCode.account_id == account.id,
            PhoneCode.created_at > now - timedelta(hours=1),
        )
        .scalar()
    )
    per_phone = (
        db.query(func.count(PhoneCode.id))
        .filter(
            PhoneCode.phone == phone, PhoneCode.created_at > now - timedelta(days=1)
        )
        .scalar()
    )
    sent_today = (
        db.query(func.count(PhoneCode.id))
        .filter(PhoneCode.created_at > now - timedelta(days=1))
        .scalar()
    )
    if (
        per_account >= MAX_CODES_PER_HOUR
        or per_phone >= MAX_CODES_PER_PHONE_PER_DAY
        or sent_today >= settings.SMS_DAILY_LIMIT
    ):
        db.commit()
        logger.warning("phone code limit reached")
        raise HTTPException(
            status_code=429, detail="Too many codes asked for. Try again later."
        )

    db.query(PhoneCode).filter(
        PhoneCode.account_id == account.id, PhoneCode.used_at.is_(None)
    ).update({PhoneCode.used_at: now})
    code = f"{secrets.randbelow(10**6):06d}"
    db.add(
        PhoneCode(
            account_id=account.id,
            phone=phone,
            code_hash=_hash(account, phone, code),
            expires_at=now + timedelta(minutes=CODE_MINUTES),
        )
    )
    db.commit()

    try:
        sms_service.send(phone, settings.SMS_OTP_TEMPLATE.format(code=code))
    except sms_service.SmsNotConfigured:
        raise HTTPException(
            status_code=503, detail="Phone verification is not set up yet"
        ) from None
    except Exception:
        logger.exception("could not send a phone code")
        raise HTTPException(
            status_code=503, detail="Could not send the code. Try again shortly."
        ) from None


def verify_code(
    db: Session, account: CustomerAccount, raw_phone: str, code: str
) -> CustomerAccount:
    """Check the code for this number. Marks the phone verified. 400 for a bad code."""
    phone = _normalize(raw_phone)
    now = _now()
    row = (
        db.query(PhoneCode)
        .filter(
            PhoneCode.account_id == account.id,
            PhoneCode.phone == phone,
            PhoneCode.used_at.is_(None),
            PhoneCode.expires_at > now,
        )
        .order_by(PhoneCode.created_at.desc())
        .first()
    )
    if row is None or row.attempts >= MAX_ATTEMPTS:
        raise HTTPException(status_code=400, detail=BAD_CODE)
    if not hmac.compare_digest(row.code_hash, _hash(account, phone, code)):
        row.attempts += 1
        db.commit()
        raise HTTPException(status_code=400, detail=BAD_CODE)

    # Only someone holding the phone gets this far, so saying so is not a leak.
    taken = (
        db.query(CustomerAccount.id)
        .filter(
            CustomerAccount.phone == phone,
            CustomerAccount.phone_verified_at.isnot(None),
            CustomerAccount.is_active.is_(True),
            CustomerAccount.id != account.id,
        )
        .first()
    )
    if taken:
        raise HTTPException(
            status_code=409,
            detail="That number is already verified on another account.",
        )

    row.used_at = now
    account.phone = phone
    account.phone_verified_at = now
    db.commit()
    db.refresh(account)
    return account
