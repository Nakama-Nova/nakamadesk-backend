"""
Staff set their own password by an emailed one-time code, so nobody else ever types or
knows it. Two uses, one mechanism:

  * invite: the owner adds a person (name, email, role). The server gives the account a
    random password nobody knows and emails the person a code to choose their own.
  * reset: "Forgot password" on the sign-in screen emails the same kind of code.

Limits: a code lives for a limited time, allows MAX_ATTEMPTS wrong tries, works once, and
a new code replaces the old. At most MAX_PER_HOUR are sent to one person. "Forgot
password" never says whether the email belongs to an account. Setting a new password ends
every session the person had (auth_service.set_password).
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
from app.models.staff_code import StaffCode
from app.models.user import User
from app.services import auth_service, email_service

logger = logging.getLogger(__name__)

ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # no 0/O, 1/I/L: easy to read out and retype
CODE_LENGTH = 8
RESET_MINUTES = 60
INVITE_HOURS = 72
MAX_ATTEMPTS = 5
MAX_PER_HOUR = 3
BAD_CODE = "That code is wrong or has expired. Ask for a new one."
STORE_NAME = "Vriksha Studios"


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def normalize_email(email: str) -> str:
    return email.strip().lower()


def _hash(user_id, code: str) -> str:
    key = security.SECRET_KEY.encode()
    return hmac.new(
        key, f"{user_id}:{code.strip().upper()}".encode(), hashlib.sha256
    ).hexdigest()


def _user_by_email(db: Session, email: str) -> User | None:
    return (
        db.query(User)
        .filter(func.lower(User.email) == normalize_email(email))
        .filter(User.is_active.is_(True))
        .first()
    )


def issue(db: Session, user: User, purpose: str) -> None:
    """Email `user` a fresh code. 429 when too many were sent; 503 when email is not set up."""
    now = _now()
    db.query(StaffCode).filter(StaffCode.created_at < now - timedelta(days=7)).delete()
    recent = (
        db.query(func.count(StaffCode.id))
        .filter(
            StaffCode.user_id == user.id,
            StaffCode.created_at > now - timedelta(hours=1),
        )
        .scalar()
    )
    if recent >= MAX_PER_HOUR:
        db.commit()
        raise HTTPException(
            status_code=429, detail="Too many codes were sent. Try again in an hour."
        )
    db.query(StaffCode).filter(
        StaffCode.user_id == user.id, StaffCode.used_at.is_(None)
    ).update({StaffCode.used_at: now})
    code = "".join(secrets.choice(ALPHABET) for _ in range(CODE_LENGTH))
    lifetime = (
        timedelta(hours=INVITE_HOURS)
        if purpose == "invite"
        else timedelta(minutes=RESET_MINUTES)
    )
    db.add(
        StaffCode(
            user_id=user.id,
            purpose=purpose,
            code_hash=_hash(user.id, code),
            expires_at=now + lifetime,
        )
    )
    db.commit()

    name = user.full_name or user.username
    if purpose == "invite":
        subject = f"Set your {STORE_NAME} password"
        intro = (
            f"Hello {name},\n\nYou have been added to the {STORE_NAME} staff desk. "
            f"Your username is {user.username}."
        )
        valid = f"{INVITE_HOURS} hours"
    else:
        subject = f"Your {STORE_NAME} password code"
        intro = f"Hello {name},\n\nSomeone asked to reset the password for {user.username}."
        valid = f"{RESET_MINUTES} minutes"
    body = (
        f"{intro}\n\nYour code is {code}\n\n"
        'Open the staff desk, choose "Forgot password?" on the sign-in screen, then '
        f"enter your email, this code and a new password. The code works for {valid} and "
        "only once. If you did not expect this, ignore this email: nothing has changed."
    )
    try:
        email_service.send(user.email, subject, body)
    except email_service.EmailNotConfigured:
        raise HTTPException(status_code=503, detail="Email is not set up yet") from None
    except Exception:
        logger.exception("could not send a staff password code")
        raise HTTPException(
            status_code=502, detail="The email could not be sent. Try again later."
        ) from None


def forgot(db: Session, email: str) -> None:
    """Email a reset code if the address belongs to an active account. Says nothing either way."""
    user = _user_by_email(db, email)
    if user is None:
        return
    try:
        issue(db, user, "reset")
    except HTTPException:
        logger.warning("password reset code not sent for one account")


def set_password_with_code(
    db: Session, email: str, code: str, new_password: str
) -> None:
    """Check the code and set the new password. 400 for any bad, used or expired code."""
    user = _user_by_email(db, email)
    now = _now()
    row = None
    if user is not None:
        row = (
            db.query(StaffCode)
            .filter(
                StaffCode.user_id == user.id,
                StaffCode.used_at.is_(None),
                StaffCode.expires_at > now,
            )
            .order_by(StaffCode.created_at.desc())
            .first()
        )
    if row is None or row.attempts >= MAX_ATTEMPTS:
        raise HTTPException(status_code=400, detail=BAD_CODE)
    if not hmac.compare_digest(row.code_hash, _hash(user.id, code)):
        row.attempts += 1
        db.commit()
        raise HTTPException(status_code=400, detail=BAD_CODE)
    row.used_at = now
    auth_service.set_password(
        db, user, new_password, actor_id=user.id, action="PASSWORD_RESET_BY_EMAIL"
    )
