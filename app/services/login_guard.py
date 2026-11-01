"""
Slows down password guessing on /auth/login.

Failed sign-ins are recorded in the database. Three limits over the last
WINDOW_MINUTES (any one is enough to refuse):
  * the same user from the same address:  MAX_PER_USER_IP
  * any user from one address:            MAX_PER_IP
  * one user from anywhere:               MAX_PER_USER  (slow, distributed guessing)
Refusing answers 429 with Retry-After, even if the next password is right, so a
guess cannot be confirmed during the lock. A successful sign-in clears that
user's failures from that address.
"""

from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, Request
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core import ratelimit
from app.models.login_attempt import LoginAttempt

WINDOW_MINUTES = 15
MAX_PER_USER_IP = 5
MAX_PER_IP = 20
MAX_PER_USER = 30


def client_ip(request: Request) -> str:
    """The caller's address (see app.core.ratelimit.client_ip for what is trusted)."""
    return ratelimit.client_ip(request)


def _since() -> datetime:
    return datetime.now(timezone.utc) - timedelta(minutes=WINDOW_MINUTES)


def _count(db: Session, **filters) -> int:
    q = db.query(func.count(LoginAttempt.id)).filter(LoginAttempt.at > _since())
    for column, value in filters.items():
        q = q.filter(getattr(LoginAttempt, column) == value)
    return q.scalar() or 0


def check(db: Session, username: str, ip: str) -> None:
    """Raise 429 if this sign-in must wait."""
    name = username.strip().lower()
    if (
        _count(db, username=name, ip=ip) >= MAX_PER_USER_IP
        or _count(db, ip=ip) >= MAX_PER_IP
        or _count(db, username=name) >= MAX_PER_USER
    ):
        raise HTTPException(
            status_code=429,
            detail=f"Too many failed sign-in attempts. Try again in {WINDOW_MINUTES} minutes.",
            headers={"Retry-After": str(WINDOW_MINUTES * 60)},
        )


def record_failure(db: Session, username: str, ip: str) -> None:
    db.add(LoginAttempt(username=username.strip().lower(), ip=ip))
    # keep the table small: forget anything older than a day
    db.query(LoginAttempt).filter(
        LoginAttempt.at < datetime.now(timezone.utc) - timedelta(days=1)
    ).delete(synchronize_session=False)
    db.commit()


def clear(db: Session, username: str, ip: str) -> None:
    db.query(LoginAttempt).filter(
        LoginAttempt.username == username.strip().lower(), LoginAttempt.ip == ip
    ).delete(synchronize_session=False)
    db.commit()
