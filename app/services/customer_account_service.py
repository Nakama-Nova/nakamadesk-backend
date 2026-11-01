"""
Website sign-in by one-time email code. The same flow signs a shopper in and creates
the account the first time, so there is no password to store, leak or reset.

Limits: a code lives CODE_MINUTES, allows MAX_ATTEMPTS wrong tries, works once, and a
new code replaces the old. At most MAX_CODES_PER_HOUR are sent per email address (the
per-IP limit is on the route). The reply to "send me a code" never says whether the
address has an account.
"""

import hashlib
import hmac
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core import security
from app.models.customer_account import CustomerAccount, LoginCode
from app.services import email_service

logger = logging.getLogger(__name__)

CODE_MINUTES = 10
MAX_ATTEMPTS = 5
MAX_CODES_PER_HOUR = 5
BAD_CODE = "That code is wrong or has expired. Ask for a new one."
STORE_NAME = "Vriksha Studios"
TOKEN_MINUTES = 7 * 24 * 60  # how long a sign-in lasts


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def normalize_email(email: str) -> str:
    return email.strip().lower()


def _hash(email: str, code: str) -> str:
    key = security.SECRET_KEY.encode()
    return hmac.new(key, f"{email}:{code}".encode(), hashlib.sha256).hexdigest()


def request_code(db: Session, email: str) -> None:
    """Email a fresh code. Quietly sends nothing when too many were asked for."""
    email = normalize_email(email)
    now = _now()
    db.query(LoginCode).filter(LoginCode.created_at < now - timedelta(days=1)).delete()

    recent = (
        db.query(func.count(LoginCode.id))
        .filter(
            LoginCode.email == email, LoginCode.created_at > now - timedelta(hours=1)
        )
        .scalar()
    )
    if recent >= MAX_CODES_PER_HOUR:
        db.commit()
        logger.warning("sign-in code limit reached for one address")
        return

    db.query(LoginCode).filter(
        LoginCode.email == email, LoginCode.used_at.is_(None)
    ).update({LoginCode.used_at: now})
    code = f"{secrets.randbelow(10**6):06d}"
    db.add(
        LoginCode(
            email=email,
            code_hash=_hash(email, code),
            expires_at=now + timedelta(minutes=CODE_MINUTES),
        )
    )
    db.commit()

    try:
        email_service.send(
            email,
            f"Your {STORE_NAME} sign-in code: {code}",
            f"Your sign-in code is {code}.\n\n"
            f"It works for {CODE_MINUTES} minutes and only once. "
            "If you did not ask for it, you can ignore this email.",
        )
    except email_service.EmailNotConfigured:
        raise HTTPException(
            status_code=503, detail="Sign-in by email is not set up yet"
        ) from None
    except Exception:  # a mail server hiccup must not reveal anything to the caller
        logger.exception("could not send a sign-in code")


def verify_code(
    db: Session,
    email: str,
    code: str,
    name: Optional[str],
    accept_terms: bool,
) -> tuple:
    """Check a code. Returns (account, created). Raises 400 for a bad code."""
    email = normalize_email(email)
    now = _now()
    row = (
        db.query(LoginCode)
        .filter(
            LoginCode.email == email,
            LoginCode.used_at.is_(None),
            LoginCode.expires_at > now,
        )
        .order_by(LoginCode.created_at.desc())
        .first()
    )
    if row is None or row.attempts >= MAX_ATTEMPTS:
        raise HTTPException(status_code=400, detail=BAD_CODE)
    if not hmac.compare_digest(row.code_hash, _hash(email, code)):
        row.attempts += 1
        db.commit()
        raise HTTPException(status_code=400, detail=BAD_CODE)

    account = (
        db.query(CustomerAccount)
        .filter(CustomerAccount.email == email, CustomerAccount.is_active.is_(True))
        .first()
    )
    created = account is None
    if created:
        # The code stays valid, so a shopper who forgot a field can resubmit.
        if not (name and name.strip()) or not accept_terms:
            raise HTTPException(
                status_code=422,
                detail="Enter your name and accept the terms to create an account",
            )
        account = CustomerAccount(
            email=email,
            name=name.strip(),
            terms_accepted_at=now,
        )
        db.add(account)
    row.used_at = now
    account.email_verified_at = account.email_verified_at or now
    account.last_login_at = now
    try:
        db.commit()
    except IntegrityError:  # two first sign-ins at once: the other one made it
        db.rollback()
        account = (
            db.query(CustomerAccount).filter(CustomerAccount.email == email).first()
        )
        created = False
    db.refresh(account)
    return account, created


def issue_token(account: CustomerAccount) -> str:
    return security.create_scoped_token(
        account.id,
        "customer",
        TOKEN_MINUTES,
        tv=account.token_version or 0,
    )


def erase(db: Session, account: CustomerAccount) -> None:
    """
    Delete the shopper's data. The row stays (orders will point at it) but holds no
    personal data, and the old email can sign up afresh as a new account.
    """
    account.email = f"deleted-{account.id}@invalid"
    account.name = None
    account.phone = None
    account.marketing_consent = False
    account.is_active = False
    account.token_version = (account.token_version or 0) + 1
    db.commit()
