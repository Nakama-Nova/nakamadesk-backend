"""Sign-in for website customers. Staff sign-in stays in deps.py; the two never mix."""

from uuid import UUID

from fastapi import Depends, HTTPException
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.core.security import verify_token
from app.db.deps import get_db
from app.models.customer_account import CustomerAccount

customer_scheme = OAuth2PasswordBearer(tokenUrl="account/verify")


def get_current_customer(
    token: str = Depends(customer_scheme), db: Session = Depends(get_db)
) -> CustomerAccount:
    payload = verify_token(token)
    # Only tokens made for customers. A staff login has no scope, so it fails here, and
    # a customer token fails get_current_user (which refuses any scoped token).
    if payload.get("scope") != "customer":
        raise HTTPException(status_code=401, detail="Invalid token")
    try:
        account_id = UUID(payload["sub"])
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid token") from None
    account = db.get(CustomerAccount, account_id)
    if account is None or not account.is_active:
        raise HTTPException(status_code=401, detail="Account not found")
    if payload.get("tv", 0) != (account.token_version or 0):
        raise HTTPException(status_code=401, detail="Session expired, sign in again")
    return account
