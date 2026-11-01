"""
Website customer account: sign in with an emailed code, see and change your own
details, sign out everywhere, delete your data. Nothing here touches staff users.
"""

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from app.core.ratelimit import public_rate_limit
from app.db.customer_deps import get_current_customer
from app.db.deps import get_db
from app.models.customer_account import CustomerAccount
from app.schemas.account import (
    AccountOut,
    AccountUpdate,
    CodeRequest,
    CodeSent,
    TokenOut,
    VerifyRequest,
)
from app.services import customer_account_service as service

router = APIRouter(prefix="/account", tags=["Customer account"])


@router.post(
    "/request-code",
    response_model=CodeSent,
    status_code=202,
    dependencies=[Depends(public_rate_limit("account-code"))],
)
def request_code(body: CodeRequest, db: Session = Depends(get_db)):
    """Email a 6-digit code. The same reply whether or not the address has an account."""
    service.request_code(db, body.email)
    return CodeSent()


@router.post(
    "/verify",
    response_model=TokenOut,
    dependencies=[Depends(public_rate_limit("account-verify"))],
)
def verify(body: VerifyRequest, db: Session = Depends(get_db)):
    """
    Sign in with the code. The first time, send `name` and `accept_terms: true` too and
    the account is created. Returns a token for the other /account routes.
    """
    account, created = service.verify_code(
        db, body.email, body.code, body.name, body.accept_terms
    )
    return TokenOut(
        access_token=service.issue_token(account),
        expires_in=service.TOKEN_MINUTES * 60,
        is_new_account=created,
    )


@router.get("/me", response_model=AccountOut)
def me(account: CustomerAccount = Depends(get_current_customer)):
    return account


@router.patch("/me", response_model=AccountOut)
def update_me(
    body: AccountUpdate,
    account: CustomerAccount = Depends(get_current_customer),
    db: Session = Depends(get_db),
):
    """Change your name, phone (kept as typed, not verified) or marketing consent."""
    for key, value in body.model_dump(exclude_unset=True).items():
        if value is not None:
            setattr(account, key, value)
    db.commit()
    db.refresh(account)
    return account


@router.post("/logout-all", status_code=204)
def logout_all(
    account: CustomerAccount = Depends(get_current_customer),
    db: Session = Depends(get_db),
):
    """Sign out on every device, including this one."""
    account.token_version = (account.token_version or 0) + 1
    db.commit()
    return Response(status_code=204)


@router.delete("/me", status_code=204)
def delete_me(
    account: CustomerAccount = Depends(get_current_customer),
    db: Session = Depends(get_db),
):
    """Delete your account's personal data and sign out everywhere."""
    service.erase(db, account)
    return Response(status_code=204)
