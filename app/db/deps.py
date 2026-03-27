from typing import List
from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session
from app.db.session import SessionLocal
from app.core.security import verify_token, oauth2_scheme
from app.models.user import User
from app.models.enums import UserRole

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
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid token")

    user = uow.users.get_by_id(user_id)
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
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
