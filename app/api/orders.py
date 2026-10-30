"""
REST API routes for Order Management.

Exposes CRUD operations for customer orders following the
existing project conventions: RBAC via check_role, UoW injection,
PEP 257 docstrings, and explicit role lists per endpoint.

RBAC design:
  - OWNER + MANAGER: full access (create, list, view, update status)
  - SALES: can create and view orders (no status updates)
"""

from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Response

from app.db.deps import check_role, get_uow
from app.models.enums import UserRole
from app.models.user import User
from app.repositories.base import AbstractUnitOfWork
from app.schemas.order import (
    OrderCreate,
    OrderInvoiceRequest,
    OrderResponse,
    OrderStatusUpdate,
    OrderUpdate,
    PaymentCreate,
)
from app.schemas.sale import SaleResponse
from app.services import order_invoice_service, order_service

router = APIRouter(prefix="/orders", tags=["Orders"])


@router.post("/", response_model=OrderResponse, status_code=201)
def create_order(
    order_data: OrderCreate,
    current_user: User = Depends(
        check_role([UserRole.OWNER, UserRole.MANAGER, UserRole.SALES])
    ),
    uow: AbstractUnitOfWork = Depends(get_uow),
):
    """
    Create a new customer order (standard or custom).

    Standard orders reference catalogue items via item_id.
    Custom orders carry free-form specs in custom_specs and/or
    a reference_image_url, with item_name describing the piece.

    RBAC: OWNER, MANAGER, SALES.

    Args:
        order_data (OrderCreate): Validated order payload.
        current_user (User): Authenticated user performing the action.
        uow (AbstractUnitOfWork): Unit of Work for database operations.

    Returns:
        OrderResponse: The fully created order with line items.
    """
    return order_service.create_order(uow, order_data, created_by=current_user.id)


@router.get("/", response_model=List[OrderResponse])
def list_orders(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    status: Optional[str] = Query(
        None,
        description="Filter by status: draft|confirmed|in_production|ready|delivered|cancelled",
    ),
    customer_id: Optional[UUID] = None,
    order_type: Optional[str] = Query(None, description="Filter: standard|custom"),
    source: Optional[str] = Query(None, description="Filter: pos|web"),
    search: Optional[str] = Query(
        None, description="Order number, customer name or phone contains this text"
    ),
    current_user: User = Depends(
        check_role([UserRole.OWNER, UserRole.MANAGER, UserRole.SALES])
    ),
    uow: AbstractUnitOfWork = Depends(get_uow),
):
    """
    List all orders with optional filtering and pagination.

    RBAC: OWNER, MANAGER, SALES.

    Args:
        limit (int): Maximum number of orders to return.
        offset (int): Number of orders to skip.
        status (Optional[str]): Filter by lifecycle status.
        customer_id (Optional[UUID]): Filter by customer.
        order_type (Optional[str]): Filter by 'standard' or 'custom'.
        current_user (User): Authenticated user.
        uow (AbstractUnitOfWork): Unit of Work.

    Returns:
        List[OrderResponse]: Matching orders with their line items.
    """
    return order_service.list_orders(
        uow,
        limit=limit,
        offset=offset,
        status=status,
        customer_id=customer_id,
        order_type=order_type,
        source=source,
        search=search,
    )


@router.get("/{order_id}", response_model=OrderResponse)
def get_order(
    order_id: UUID,
    current_user: User = Depends(
        check_role([UserRole.OWNER, UserRole.MANAGER, UserRole.SALES])
    ),
    uow: AbstractUnitOfWork = Depends(get_uow),
):
    """
    Retrieve a specific order by its unique ID.

    RBAC: OWNER, MANAGER, SALES.

    Args:
        order_id (UUID): Unique identifier of the order.
        current_user (User): Authenticated user.
        uow (AbstractUnitOfWork): Unit of Work.

    Returns:
        OrderResponse: Detailed order information with line items.

    Raises:
        HTTPException 404: If the order does not exist.
    """
    order = order_service.get_order(uow, order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    return order


@router.patch("/{order_id}/status", response_model=OrderResponse)
def update_order_status(
    order_id: UUID,
    update_data: OrderStatusUpdate,
    current_user: User = Depends(check_role([UserRole.OWNER, UserRole.MANAGER])),
    uow: AbstractUnitOfWork = Depends(get_uow),
):
    """
    Transition an order to a new lifecycle status.

    Enforces forward-only state machine transitions:
        draft → confirmed → in_production → ready → delivered
        Any → cancelled

    RBAC: OWNER, MANAGER only (SALES cannot change order status).

    Args:
        order_id (UUID): Unique identifier of the order.
        update_data (OrderStatusUpdate): New status and optional note.
        current_user (User): Authenticated user.
        uow (AbstractUnitOfWork): Unit of Work.

    Returns:
        OrderResponse: The updated order.

    Raises:
        HTTPException 404: If order not found.
        HTTPException 400: If the transition is not permitted.
    """
    return order_service.update_order_status(uow, order_id, update_data)


@router.post("/{order_id}/payments", response_model=OrderResponse, status_code=201)
def add_order_payment(
    order_id: UUID,
    data: PaymentCreate,
    current_user: User = Depends(
        check_role([UserRole.OWNER, UserRole.MANAGER, UserRole.SALES])
    ),
    uow: AbstractUnitOfWork = Depends(get_uow),
):
    """
    Record money received against an order (advance, instalment or the balance).
    The order's advance_paid becomes the sum of its payments. RBAC: OWNER, MANAGER, SALES.
    """
    return order_service.add_payment(uow, order_id, data, current_user.id)


@router.patch("/{order_id}", response_model=OrderResponse)
def update_order(
    order_id: UUID,
    data: OrderUpdate,
    current_user: User = Depends(check_role([UserRole.OWNER, UserRole.MANAGER])),
    uow: AbstractUnitOfWork = Depends(get_uow),
):
    """Edit the amounts, expected delivery or notes of an open order. RBAC: OWNER, MANAGER."""
    return order_service.update_order(uow, order_id, data)


@router.post("/{order_id}/invoice", response_model=SaleResponse, status_code=201)
def invoice_order(
    order_id: UUID,
    response: Response,
    req: OrderInvoiceRequest = Body(default_factory=OrderInvoiceRequest),
    current_user: User = Depends(
        check_role([UserRole.OWNER, UserRole.MANAGER, UserRole.SALES])
    ),
    uow: AbstractUnitOfWork = Depends(get_uow),
):
    """
    Make the bill (invoice) for an order: its pieces at the prices agreed on the order,
    stock taken off for catalogue pieces, the payments already received carried over, and
    the order linked to the bill. Asking again returns the same bill (200).

    RBAC: OWNER, MANAGER, SALES.
    """
    sale, created = order_invoice_service.invoice_order(
        uow, order_id, current_user.id, allow_negative_stock=req.allow_negative_stock
    )
    if not created:
        response.status_code = 200
    return sale
