"""
What a signed-in shopper can do beyond signing in: keep delivery addresses, place
orders that are tied to the account, and look at their own orders.

Orders go through the same public checkout and enquiry code as guests, so prices,
stock checks and the pincode rule are identical; the only difference is that the
order records the account. A shopper only ever sees orders and addresses tied to
their own account (a phone number alone proves nothing here).
"""

from typing import List, Optional
from uuid import UUID

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy.orm import Session, joinedload

from app.models.customer_account import CustomerAccount, CustomerAddress
from app.models.order import Order
from app.repositories.base import AbstractUnitOfWork
from app.schemas.account import (
    AccountCheckoutRequest,
    AccountEnquiryRequest,
    AddressIn,
)
from app.schemas.public import CheckoutRequest, EnquiryRequest, PublicOrder
from app.services import public_service

MAX_ADDRESSES = 10


# ----------------------------------------------------------------------- addresses
def _own_address(db: Session, account: CustomerAccount, address_id: UUID):
    row = (
        db.query(CustomerAddress)
        .filter(
            CustomerAddress.id == address_id, CustomerAddress.account_id == account.id
        )
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Address not found")
    return row


def list_addresses(db: Session, account: CustomerAccount) -> List[CustomerAddress]:
    return (
        db.query(CustomerAddress)
        .filter(CustomerAddress.account_id == account.id)
        .order_by(CustomerAddress.is_default.desc(), CustomerAddress.created_at)
        .all()
    )


def _make_default(db: Session, account: CustomerAccount, row: CustomerAddress) -> None:
    db.query(CustomerAddress).filter(
        CustomerAddress.account_id == account.id, CustomerAddress.id != row.id
    ).update({CustomerAddress.is_default: False})
    row.is_default = True


def add_address(
    db: Session, account: CustomerAccount, data: AddressIn
) -> CustomerAddress:
    existing = (
        db.query(CustomerAddress)
        .filter(CustomerAddress.account_id == account.id)
        .count()
    )
    if existing >= MAX_ADDRESSES:
        raise HTTPException(
            status_code=400, detail=f"You can save up to {MAX_ADDRESSES} addresses"
        )
    row = CustomerAddress(
        account_id=account.id, **data.model_dump(exclude={"is_default"})
    )
    db.add(row)
    db.flush()
    if data.is_default or existing == 0:
        _make_default(db, account, row)
    db.commit()
    db.refresh(row)
    return row


def update_address(
    db: Session, account: CustomerAccount, address_id: UUID, data: AddressIn
) -> CustomerAddress:
    row = _own_address(db, account, address_id)
    for key, value in data.model_dump(exclude={"is_default"}).items():
        setattr(row, key, value)
    if data.is_default:
        _make_default(db, account, row)
    db.commit()
    db.refresh(row)
    return row


def delete_address(db: Session, account: CustomerAccount, address_id: UUID) -> None:
    row = _own_address(db, account, address_id)
    was_default = row.is_default
    db.delete(row)
    db.flush()
    if was_default:  # keep one default: the newest of the rest
        newest = (
            db.query(CustomerAddress)
            .filter(CustomerAddress.account_id == account.id)
            .order_by(CustomerAddress.created_at.desc())
            .first()
        )
        if newest is not None:
            newest.is_default = True
    db.commit()


# ------------------------------------------------------------------------- orders
def _need(value: Optional[str], what: str) -> str:
    if not value:
        raise HTTPException(status_code=422, detail=f"Add {what} to place an order")
    return value


def _validated(model, **fields):
    try:
        return model(**fields)
    except ValidationError as e:
        first = e.errors()[0]
        where = ".".join(str(p) for p in first["loc"])
        raise HTTPException(
            status_code=422, detail=f"{where}: {first['msg']}"
        ) from None


def checkout(
    uow: AbstractUnitOfWork, account: CustomerAccount, req: AccountCheckoutRequest
) -> PublicOrder:
    """Place a draft order for the account, to a saved address or a one-off one."""
    if req.address_id is not None:
        row = _own_address(uow.session, account, req.address_id)
        recipient, phone, line = row.recipient_name, row.phone, row
    else:
        recipient, phone, line = (
            req.address.recipient_name,
            req.address.phone,
            req.address,
        )
    guest = _validated(
        CheckoutRequest,
        name=_need(req.name or recipient or account.name, "your name"),
        phone=_need(req.phone or phone or account.phone, "a phone number"),
        email=account.email,
        address=line.address,
        town=line.town,
        pincode=line.pincode,
        note=req.note,
        pay_option=req.pay_option,
        items=req.items,
    )
    return public_service.checkout(uow, guest, account_id=account.id)


def enquiry(
    uow: AbstractUnitOfWork, account: CustomerAccount, req: AccountEnquiryRequest
) -> PublicOrder:
    guest = _validated(
        EnquiryRequest,
        name=_need(req.name or account.name, "your name"),
        phone=_need(req.phone or account.phone, "a phone number"),
        what=req.what,
        description=req.description,
        wood=req.wood,
        size=req.size,
        budget=req.budget,
    )
    return public_service.enquiry(uow, guest, account_id=account.id)


def list_orders(
    uow: AbstractUnitOfWork, account: CustomerAccount, limit: int, offset: int
) -> List[PublicOrder]:
    rows = (
        uow.session.query(Order)
        .options(joinedload(Order.items))
        .filter(Order.account_id == account.id)
        .order_by(Order.order_date.desc(), Order.id)
        .limit(limit)
        .offset(offset)
        .all()
    )
    return [public_service.to_public_order(o) for o in rows]


def get_order(
    uow: AbstractUnitOfWork, account: CustomerAccount, order_number: str
) -> PublicOrder:
    order = (
        uow.session.query(Order)
        .options(joinedload(Order.items))
        .filter(
            Order.order_number == order_number.strip().upper(),
            Order.account_id == account.id,
        )
        .first()
    )
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")
    return public_service.to_public_order(order)
