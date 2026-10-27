from typing import List

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.core.security import oauth2_scheme, verify_token
from app.db.session import SessionLocal
from app.models.enums import UserRole
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
    return user


def check_role(allowed_roles: List[UserRole]):
    def role_checker(current_user: User = Depends(get_current_user)):
        if current_user.role not in allowed_roles:
            raise HTTPException(
                status_code=403,
                detail=f"Operation not permitted for role: {current_user.role}. Required: {allowed_roles}",
            )
        return current_user

    return role_checker


LAB_COOKIE = "lab_session"
# Temple restorations (projects, scans, 3D results) are for the owner and artisans only.
RESTORATION_ROLES = [UserRole.OWNER, UserRole.ARTISAN]
LAB_ROLES = [
    UserRole.OWNER,
    UserRole.MANAGER,
    UserRole.ACHARI,
    UserRole.WORKER,
    UserRole.ARTISAN,
]
LAB_EDIT_ROLES = [UserRole.OWNER, UserRole.MANAGER]


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
    if user.role not in LAB_ROLES:
        raise HTTPException(status_code=403, detail="Not permitted for your role")
    return user


def get_lab_editor(user: User = Depends(get_lab_user)) -> User:
    if user.role not in LAB_EDIT_ROLES:
        raise HTTPException(
            status_code=403, detail="Only the owner or a manager can edit parts"
        )
    return user
