"""
Business logic behind the public website API.

Everything here treats the caller as an anonymous stranger: prices always come
from the database (never from the request), stock is only checked (staff
confirm the order and the sale moves stock), and an order can only be read back
with its order number AND the phone number it was placed with.
"""

import re
from decimal import Decimal
from typing import Dict, List, Optional

from fastapi import HTTPException
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload

from app.core.config import settings
from app.models.category import Category
from app.models.customer import Customer
from app.models.item import Item
from app.models.order import Order
from app.models.order_item import OrderItem
from app.models.product_asset import ProductAsset
from app.repositories.base import AbstractUnitOfWork
from app.schemas.public import (
    CheckoutRequest,
    EnquiryRequest,
    PublicImage,
    PublicOrder,
    PublicOrderLine,
    PublicProduct,
)
from app.services import media_service
from app.services.order_service import _generate_order_number


# --------------------------------------------------------------------------- helpers
def normalize_phone(raw: str) -> str:
    """10-digit Indian mobile from +91 98765 43210 / 09876543210 / 9876543210."""
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    if not re.fullmatch(r"[6-9]\d{9}", digits):
        raise HTTPException(
            status_code=422, detail="Enter a valid 10-digit mobile number"
        )
    return digits


def is_serviceable(pincode: str) -> bool:
    allowed = settings.DELIVERY_PINCODES
    return not allowed or pincode in allowed  # empty list = no restriction yet


def _find_customer(uow: AbstractUnitOfWork, phone: str) -> Optional[Customer]:
    return (
        uow.session.query(Customer)
        .filter(Customer.phone.in_([phone, f"+91{phone}", f"91{phone}", f"0{phone}"]))
        .first()
    )


# --------------------------------------------------------------------------- catalogue
def _to_product(item: Item, assets: List[ProductAsset], category: Optional[str]):
    # `assets` is already limited to public, server-disk files (see _products_for)
    def pictures(kind: str):
        return [
            PublicImage(url=media_service.url(a.storage_key), alt=a.alt)
            for a in assets
            if a.kind == kind
        ]

    images, renders = pictures("image"), pictures("render")
    model = next((a for a in assets if a.kind == "glb"), None)
    return PublicProduct(
        slug=item.slug,
        name=item.name,
        item_type=item.item_type or "furniture",
        short_desc=item.short_desc,
        price=item.selling_price,
        made_to_order=item.made_to_order,
        lead_time_days=item.lead_time_days,
        in_stock=(item.current_stock or 0) > 0,
        wood_type=item.wood_type,
        dimensions_mm=item.dimensions_mm,
        category=category,
        images=images,
        renders=renders,
        model_url=media_service.url(model.storage_key) if model else None,
    )


def _published():
    return (
        Item.is_published.is_(True),
        Item.is_active.is_(True),
        Item.slug.isnot(None),
    )


def list_catalog(
    uow: AbstractUnitOfWork,
    q: Optional[str],
    category: Optional[str],
    limit: int,
    offset: int,
) -> List[PublicProduct]:
    query = uow.session.query(Item).filter(*_published())
    if q:
        like = f"%{q.strip()}%"
        query = query.filter(or_(Item.name.ilike(like), Item.short_desc.ilike(like)))
    if category:
        cat_ids = [
            c.id
            for c in uow.session.query(Category).filter(Category.name.ilike(category))
        ]
        query = query.filter(Item.category_id.in_(cat_ids or [None]))
    items = query.order_by(Item.name).limit(limit).offset(offset).all()
    return _products_for(uow, items)


def _products_for(uow: AbstractUnitOfWork, items: List[Item]) -> List[PublicProduct]:
    if not items:
        return []
    ids = [i.id for i in items]
    assets: Dict = {i: [] for i in ids}
    for a in (
        uow.session.query(ProductAsset)
        .filter(
            ProductAsset.item_id.in_(ids),
            # THE gate: staff-only and private-store files never reach the website
            ProductAsset.visibility == "public",
            ProductAsset.storage == "local",
        )
        .order_by(ProductAsset.sort, ProductAsset.created_at)
    ):
        assets[a.item_id].append(a)
    cat_ids = {i.category_id for i in items if i.category_id}
    cats = (
        {
            c.id: c.name
            for c in uow.session.query(Category).filter(Category.id.in_(cat_ids))
        }
        if cat_ids
        else {}
    )
    return [_to_product(i, assets[i.id], cats.get(i.category_id)) for i in items]


def get_product(uow: AbstractUnitOfWork, slug: str) -> PublicProduct:
    item = uow.session.query(Item).filter(Item.slug == slug, *_published()).first()
    if not item:
        raise HTTPException(status_code=404, detail="Product not found")
    return _products_for(uow, [item])[0]


# --------------------------------------------------------------------------- orders
def _new_order_with_retry(uow: AbstractUnitOfWork, build) -> Order:
    """Order numbers are count+1; two simultaneous orders can collide, so retry."""
    for attempt in range(3):
        try:
            with uow:
                order = build(_generate_order_number(uow))
                uow.session.add(order)
                uow.commit()
            return order
        except IntegrityError:
            uow.rollback()
            if attempt == 2:
                raise HTTPException(
                    status_code=409, detail="Busy right now, please try again"
                ) from None
    raise AssertionError("unreachable")


def _customer_for(
    uow: AbstractUnitOfWork,
    name: str,
    phone: str,
    email=None,
    address=None,
    pincode=None,
) -> Customer:
    customer = _find_customer(uow, phone)
    if not customer:
        customer = Customer(
            name=name.strip(),
            phone=phone,
            email=email,
            address=address,
            pincode=pincode,
        )
        uow.session.add(customer)
        uow.flush()
    return customer


def checkout(uow: AbstractUnitOfWork, req: CheckoutRequest) -> PublicOrder:
    phone = normalize_phone(req.phone)
    if not is_serviceable(req.pincode):
        raise HTTPException(
            status_code=400, detail="Sorry, we do not deliver to that pincode yet"
        )

    wanted: Dict[str, int] = {}
    for line in req.items:
        wanted[line.slug] = wanted.get(line.slug, 0) + line.quantity
    items = uow.session.query(Item).filter(Item.slug.in_(wanted), *_published()).all()
    by_slug = {i.slug: i for i in items}
    for slug, qty in wanted.items():
        item = by_slug.get(slug)
        if item is None:
            raise HTTPException(status_code=400, detail=f"'{slug}' is not available")
        if not item.made_to_order and (item.current_stock or 0) < qty:
            raise HTTPException(
                status_code=400, detail=f"Not enough stock for {item.name}"
            )
        if req.pay_option == "on_delivery" and item.made_to_order:
            raise HTTPException(
                status_code=400,
                detail=f"{item.name} is made to order: pay an advance by UPI instead",
            )

    total = sum((by_slug[s].selling_price * q for s, q in wanted.items()), Decimal("0"))
    customer = _customer_for(uow, req.name, phone, req.email, req.address, req.pincode)
    address = {
        "address": req.address,
        "town": req.town,
        "pincode": req.pincode,
        "note": req.note,
        "pay_option": req.pay_option,
    }

    def build(number: str) -> Order:
        return Order(
            order_number=number,
            customer_id=customer.id,
            order_type="standard",
            status="draft",
            source="web",
            delivery_address=address,
            estimated_amount=total,
            created_by=None,
            items=[
                OrderItem(
                    item_id=by_slug[s].id,
                    item_name=by_slug[s].name,
                    quantity=q,
                    unit_price=by_slug[s].selling_price,
                )
                for s, q in wanted.items()
            ],
        )

    return _to_public_order(_new_order_with_retry(uow, build))


def enquiry(uow: AbstractUnitOfWork, req: EnquiryRequest) -> PublicOrder:
    phone = normalize_phone(req.phone)
    customer = _customer_for(uow, req.name, phone)
    specs = {
        "what": req.what,
        "description": req.description,
        "wood": req.wood,
        "size": req.size,
        "budget": req.budget,
    }

    def build(number: str) -> Order:
        return Order(
            order_number=number,
            customer_id=customer.id,
            order_type="custom",
            status="draft",
            source="web",
            custom_specs=specs,
            estimated_amount=0,
            created_by=None,
            items=[OrderItem(item_name=req.what, quantity=1, unit_price=0)],
        )

    return _to_public_order(_new_order_with_retry(uow, build))


def track(uow: AbstractUnitOfWork, order_number: str, phone: str) -> PublicOrder:
    """404 for an unknown number AND for a wrong phone, so numbers can't be probed."""
    not_found = HTTPException(status_code=404, detail="Order not found")
    try:
        wanted = normalize_phone(phone)
    except HTTPException:
        raise not_found from None
    order = (
        uow.session.query(Order)
        .options(joinedload(Order.items), joinedload(Order.customer))
        .filter(Order.order_number == order_number.strip().upper())
        .first()
    )
    if not order or not order.customer:
        raise not_found
    try:
        owner = normalize_phone(order.customer.phone or "")
    except HTTPException:
        raise not_found from None
    if owner != wanted:
        raise not_found
    return _to_public_order(order)


def _to_public_order(order: Order) -> PublicOrder:
    zero = Decimal("0.00")
    final = order.final_amount or zero
    base = final if final > 0 else (order.estimated_amount or zero)
    advance = order.advance_paid or zero
    return PublicOrder(
        order_number=order.order_number,
        status=order.status,
        order_type=order.order_type,
        placed_on=order.order_date,
        expected_delivery=order.expected_delivery,
        total=base,
        advance_paid=advance,
        balance_due=max(zero, base - advance),
        items=[
            PublicOrderLine(
                name=i.item_name or (i.item.name if i.item else "Item"),
                quantity=i.quantity,
                unit_price=i.unit_price,
            )
            for i in order.items
        ],
    )
