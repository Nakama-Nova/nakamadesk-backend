"""
Public website API: no login, read-mostly, rate limited.

Staff never call these; the storefront does. Costs, stock counts and other
customers' data are never returned.
"""

from typing import List, Optional

from fastapi import APIRouter, Depends, Query

from app.core.ratelimit import public_rate_limit
from app.db.deps import get_uow
from app.repositories.base import AbstractUnitOfWork
from app.schemas.public import (
    CheckoutRequest,
    DeliveryCheck,
    EnquiryRequest,
    PublicOrder,
    PublicProduct,
)
from app.services import public_service

router = APIRouter(prefix="/public", tags=["Public website"])


@router.get("/catalog", response_model=List[PublicProduct])
def catalog(
    q: Optional[str] = Query(
        None, max_length=100, description="Search name/description"
    ),
    category: Optional[str] = Query(None, max_length=100),
    limit: int = Query(24, ge=1, le=50),
    offset: int = Query(0, ge=0),
    uow: AbstractUnitOfWork = Depends(get_uow),
):
    """Published products, A to Z."""
    return public_service.list_catalog(uow, q, category, limit, offset)


@router.get("/catalog/{slug}", response_model=PublicProduct)
def product(slug: str, uow: AbstractUnitOfWork = Depends(get_uow)):
    """One published product with its photos and (if any) 3D model."""
    return public_service.get_product(uow, slug)


@router.get("/delivery-check", response_model=DeliveryCheck)
def delivery_check(pincode: str = Query(..., pattern=r"^\d{6}$")):
    """Do we deliver to this pincode?"""
    return DeliveryCheck(
        pincode=pincode, serviceable=public_service.is_serviceable(pincode)
    )


@router.post(
    "/checkout",
    response_model=PublicOrder,
    status_code=201,
    dependencies=[Depends(public_rate_limit("checkout"))],
)
def checkout(req: CheckoutRequest, uow: AbstractUnitOfWork = Depends(get_uow)):
    """
    Guest checkout. Creates a draft order; nothing is charged. Staff confirm by
    phone, then the customer pays an advance by UPI. Prices come from the server.
    """
    return public_service.checkout(uow, req)


@router.post(
    "/enquiry",
    response_model=PublicOrder,
    status_code=201,
    dependencies=[Depends(public_rate_limit("enquiry"))],
)
def enquiry(req: EnquiryRequest, uow: AbstractUnitOfWork = Depends(get_uow)):
    """Custom piece request. Creates a draft custom order for staff to quote."""
    return public_service.enquiry(uow, req)


@router.get(
    "/orders/{order_number}",
    response_model=PublicOrder,
    dependencies=[Depends(public_rate_limit("track"))],
)
def track_order(
    order_number: str,
    phone: str = Query(..., max_length=20),
    uow: AbstractUnitOfWork = Depends(get_uow),
):
    """Order status. Needs the order number AND the phone it was placed with."""
    return public_service.track(uow, order_number, phone)
