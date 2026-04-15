"""
Service layer for Order Management.

Implements the core business logic for creating, retrieving, and
updating the lifecycle of customer orders. Follows the same UoW
(Unit of Work) pattern used in sales_service.py and inventory_service.py.

No inventory deduction or invoice generation occurs here — those
are Day 2 and Day 3 concerns.
"""

import logging
from datetime import datetime, timezone
from typing import List, Optional
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import joinedload

from app.models.order import Order
from app.models.order_item import OrderItem
from app.repositories.base import AbstractUnitOfWork
from app.schemas.order import OrderCreate, OrderStatusUpdate

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Order number generation
# ---------------------------------------------------------------------------


def _generate_order_number(uow: AbstractUnitOfWork) -> str:
    """
    Generate a sequential, human-readable order number.

    Format: ORD-YYYY-NNNN (e.g. ORD-2026-0001).
    The counter increments per calendar year based on existing DB rows.

    Args:
        uow (AbstractUnitOfWork): Active unit of work.

    Returns:
        str: A unique order number string.
    """
    year = datetime.now(timezone.utc).year
    count = (
        uow.session.query(func.count(Order.id))
        .filter(Order.order_number.like(f"ORD-{year}-%"))
        .scalar()
        or 0
    )
    return f"ORD-{year}-{count + 1:04d}"


# ---------------------------------------------------------------------------
# Core order CRUD
# ---------------------------------------------------------------------------


def create_order(
    uow: AbstractUnitOfWork,
    order_data: OrderCreate,
    created_by: UUID,
) -> Order:
    """
    Create a new customer order along with all its line items.

    Validates that the customer exists (if provided) and that
    any item_id references in line items resolve to active catalogue items.

    Args:
        uow (AbstractUnitOfWork): Unit of Work for database operations.
        order_data (OrderCreate): Validated order payload from the API.
        created_by (UUID): ID of the authenticated user creating the order.

    Returns:
        Order: The fully persisted and refreshed order object.

    Raises:
        HTTPException 404: If customer_id or an item_id does not exist.
    """
    with uow:
        # Validate customer exists (if supplied)
        if order_data.customer_id:
            customer = uow.customers.get_by_id(order_data.customer_id)
            if not customer:
                raise HTTPException(
                    status_code=404,
                    detail=f"Customer {order_data.customer_id} not found",
                )

        # Validate catalogue items in line items
        for line in order_data.items:
            if line.item_id:
                item = uow.items.get_by_id(line.item_id)
                if not item:
                    raise HTTPException(
                        status_code=404,
                        detail=f"Item {line.item_id} not found in catalogue",
                    )

        # Generate order number
        order_number = _generate_order_number(uow)

        # Build Order record
        new_order = Order(
            order_number=order_number,
            customer_id=order_data.customer_id,
            order_type=order_data.order_type,
            status="draft",
            custom_specs=order_data.custom_specs,
            reference_image_url=order_data.reference_image_url,
            estimated_amount=order_data.estimated_amount,
            advance_paid=order_data.advance_paid,
            final_amount=0,
            expected_delivery=order_data.expected_delivery,
            created_by=created_by,
            notes=order_data.notes,
        )
        uow.session.add(new_order)
        uow.flush()  # Materialise new_order.id for FK references below

        # Build OrderItem records
        for line in order_data.items:
            order_item = OrderItem(
                order_id=new_order.id,
                item_id=line.item_id,
                item_name=line.item_name,
                quantity=line.quantity,
                unit_price=line.unit_price,
                notes=line.notes,
            )
            uow.session.add(order_item)

        uow.commit()

    uow.refresh(new_order)
    logger.info(
        "Order created: %s (ID: %s) by user %s",
        new_order.order_number,
        new_order.id,
        created_by,
    )
    return get_order(uow, new_order.id)  # Eager-load for response


def get_order(uow: AbstractUnitOfWork, order_id: UUID) -> Optional[Order]:
    """
    Retrieve a single order by ID, eagerly loading its line items.

    Args:
        uow (AbstractUnitOfWork): Unit of Work for database access.
        order_id (UUID): Unique ID of the order.

    Returns:
        Optional[Order]: The order with items loaded, or None.
    """
    return (
        uow.session.query(Order)
        .options(joinedload(Order.items))
        .filter(Order.id == order_id)
        .first()
    )


def list_orders(
    uow: AbstractUnitOfWork,
    limit: int = 50,
    offset: int = 0,
    status: Optional[str] = None,
    customer_id: Optional[UUID] = None,
    order_type: Optional[str] = None,
) -> List[Order]:
    """
    List orders with optional filtering and pagination.

    Args:
        uow (AbstractUnitOfWork): Unit of Work for database access.
        limit (int): Maximum records to return (1-100).
        offset (int): Records to skip.
        status (Optional[str]): Filter by lifecycle status.
        customer_id (Optional[UUID]): Filter by customer.
        order_type (Optional[str]): Filter by 'standard' or 'custom'.

    Returns:
        List[Order]: Matching orders with items loaded.
    """
    query = uow.session.query(Order).options(joinedload(Order.items))

    if status:
        query = query.filter(Order.status == status)
    if customer_id:
        query = query.filter(Order.customer_id == customer_id)
    if order_type:
        query = query.filter(Order.order_type == order_type)

    return query.order_by(Order.created_at.desc()).limit(limit).offset(offset).all()


def update_order_status(
    uow: AbstractUnitOfWork,
    order_id: UUID,
    update_data: OrderStatusUpdate,
) -> Order:
    """
    Transition an order to a new lifecycle status.

    Enforces valid forward-only transitions and records delivery
    timestamp when an order reaches 'delivered'.

    Valid transitions:
        draft -> confirmed -> in_production -> ready -> delivered
        Any -> cancelled

    Args:
        uow (AbstractUnitOfWork): Unit of Work.
        order_id (UUID): Unique ID of the order to update.
        update_data (OrderStatusUpdate): New status and optional note.

    Returns:
        Order: The updated order.

    Raises:
        HTTPException 404: If the order does not exist.
        HTTPException 400: If the transition is not permitted.
    """
    # Define allowed forward transitions
    _ALLOWED: dict = {
        "draft": {"confirmed", "cancelled"},
        "confirmed": {"in_production", "cancelled"},
        "in_production": {"ready", "cancelled"},
        "ready": {"delivered", "cancelled"},
        "delivered": set(),  # terminal
        "cancelled": set(),  # terminal
    }

    with uow:
        order = uow.session.query(Order).filter(Order.id == order_id).first()
        if not order:
            raise HTTPException(status_code=404, detail="Order not found")

        allowed = _ALLOWED.get(order.status, set())
        if update_data.status not in allowed:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Cannot transition order from '{order.status}' "
                    f"to '{update_data.status}'. "
                    f"Allowed transitions: "
                    f"{sorted(allowed) or 'none (terminal state)'}"
                ),
            )

        order.status = update_data.status
        if update_data.notes:
            order.notes = update_data.notes
        if update_data.status == "delivered":
            order.delivered_at = datetime.now(timezone.utc)

        uow.commit()

    uow.refresh(order)
    logger.info("Order %s transitioned to '%s'", order.order_number, order.status)
    return get_order(uow, order.id)
