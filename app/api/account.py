"""
Website customer account: sign in with an emailed code, see and change your own
details, sign out everywhere, delete your data. Nothing here touches staff users.
"""

from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from app.core.ratelimit import public_rate_limit
from app.db.customer_deps import get_current_customer
from app.db.deps import get_db, get_uow
from app.models.customer_account import CustomerAccount
from app.repositories.base import AbstractUnitOfWork
from app.schemas.account import (
    AccountCheckoutRequest,
    AccountEnquiryRequest,
    AccountOut,
    AccountUpdate,
    AddressIn,
    AddressOut,
    CodeRequest,
    CodeSent,
    TokenOut,
    VerifyRequest,
)
from app.schemas.public import PublicOrder
from app.services import account_shop_service as shop
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


# ------------------------------------------------------------------ saved addresses
@router.get("/addresses", response_model=List[AddressOut])
def list_addresses(
    account: CustomerAccount = Depends(get_current_customer),
    db: Session = Depends(get_db),
):
    """Your saved delivery addresses, the default first."""
    return shop.list_addresses(db, account)


@router.post("/addresses", response_model=AddressOut, status_code=201)
def add_address(
    body: AddressIn,
    account: CustomerAccount = Depends(get_current_customer),
    db: Session = Depends(get_db),
):
    """Save an address (up to 10). The first one saved becomes the default."""
    return shop.add_address(db, account, body)


@router.put("/addresses/{address_id}", response_model=AddressOut)
def update_address(
    address_id: UUID,
    body: AddressIn,
    account: CustomerAccount = Depends(get_current_customer),
    db: Session = Depends(get_db),
):
    return shop.update_address(db, account, address_id, body)


@router.delete("/addresses/{address_id}", status_code=204)
def delete_address(
    address_id: UUID,
    account: CustomerAccount = Depends(get_current_customer),
    db: Session = Depends(get_db),
):
    shop.delete_address(db, account, address_id)
    return Response(status_code=204)


# ---------------------------------------------------------------------------- orders
@router.post(
    "/checkout",
    response_model=PublicOrder,
    status_code=201,
    dependencies=[Depends(public_rate_limit("account-checkout"))],
)
def checkout(
    body: AccountCheckoutRequest,
    account: CustomerAccount = Depends(get_current_customer),
    uow: AbstractUnitOfWork = Depends(get_uow),
):
    """
    Place an order as a signed-in shopper. Same rules as guest checkout (server
    prices, stock check, pincode check, nothing is charged), but the order belongs to
    your account and shows up in `GET /account/orders`.
    """
    return shop.checkout(uow, account, body)


@router.post(
    "/enquiry",
    response_model=PublicOrder,
    status_code=201,
    dependencies=[Depends(public_rate_limit("account-enquiry"))],
)
def enquiry(
    body: AccountEnquiryRequest,
    account: CustomerAccount = Depends(get_current_customer),
    uow: AbstractUnitOfWork = Depends(get_uow),
):
    """Ask for a custom piece as a signed-in shopper."""
    return shop.enquiry(uow, account, body)


@router.get("/orders", response_model=List[PublicOrder])
def my_orders(
    limit: int = Query(20, ge=1, le=50),
    offset: int = Query(0, ge=0),
    account: CustomerAccount = Depends(get_current_customer),
    uow: AbstractUnitOfWork = Depends(get_uow),
):
    """Orders placed while signed in to this account, newest first."""
    return shop.list_orders(uow, account, limit, offset)


@router.get("/orders/{order_number}", response_model=PublicOrder)
def my_order(
    order_number: str,
    account: CustomerAccount = Depends(get_current_customer),
    uow: AbstractUnitOfWork = Depends(get_uow),
):
    """One of your orders. 404 for any order that is not yours."""
    return shop.get_order(uow, account, order_number)
