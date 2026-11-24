from datetime import date

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.core.security import oauth2_scheme, verify_token
from app.db.session import SessionLocal
from app.models.user import User
from app.repositories.base import AbstractUnitOfWork
from app.repositories.sqlalchemy_repo import SQLAlchemyUnitOfWork


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_uow(db: Session = Depends(get_db)):
    return SQLAlchemyUnitOfWork(db)


def check_token_version(payload: dict, user: User) -> None:
    """A token made before the user's password/role/status last changed is dead.
    Tokens from before this existed carry no `tv`, which counts as version 0."""
    if payload.get("tv", 0) != (user.token_version or 0):
        raise HTTPException(status_code=401, detail="Session expired, sign in again")


def get_current_user(
    token: str = Depends(oauth2_scheme), uow: AbstractUnitOfWork = Depends(get_uow)
) -> User:
    payload = verify_token(token)
    if payload.get("scope"):
        # single-purpose tokens (Lab links) are not logins
        raise HTTPException(status_code=401, detail="Invalid token")
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid token")

    user = uow.users.get_by_id(user_id)
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    if not user.is_active:
        raise HTTPException(status_code=401, detail="Account is switched off")
    if user.last_working_date and user.last_working_date < date.today():
        # offboarded: the account goes off the day after the last working day
        raise HTTPException(status_code=401, detail="Account is switched off")
    check_token_version(payload, user)
    return user


LAB_COOKIE = "lab_session"


def get_lab_user(
    request: Request,
    uow: AbstractUnitOfWork = Depends(get_uow),
) -> User:
    """
    Who is using the 3D Lab: a normal Bearer login (apps) or the HttpOnly Lab
    cookie (the browser viewer). The cookie holds a scoped, short-lived token.
    Cookie requests that CHANGE data must also send `X-Lab-Client: 1`, which a
    cross-site page cannot do, so a foreign site cannot make the browser edit.
    """
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        payload = verify_token(auth[7:].strip())
        if payload.get("scope"):
            raise HTTPException(status_code=401, detail="Invalid token")
    else:
        cookie = request.cookies.get(LAB_COOKIE)
        if not cookie:
            raise HTTPException(status_code=401, detail="Open the Lab from the app")
        payload = verify_token(cookie)
        if payload.get("scope") != "lab":
            raise HTTPException(status_code=401, detail="Invalid Lab session")
        if (
            request.method not in ("GET", "HEAD", "OPTIONS")
            and request.headers.get("x-lab-client") != "1"
        ):
            raise HTTPException(status_code=403, detail="Missing Lab client header")
    user = uow.users.get_by_id(payload.get("sub"))
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="User not found")
    check_token_version(payload, user)
    from app.iam import service as iam  # late: iam imports this module

    if not iam.allowed(uow.session, user, "lab:part:read"):
        raise HTTPException(status_code=403, detail="Not permitted for your role")
    return user


def get_lab_editor(
    user: User = Depends(get_lab_user), uow: AbstractUnitOfWork = Depends(get_uow)
) -> User:
    from app.iam import service as iam  # late: iam imports this module

    if not iam.allowed(uow.session, user, "lab:part:edit"):
        raise HTTPException(
            status_code=403, detail="Only the owner or a manager can edit parts"
        )
    return user
