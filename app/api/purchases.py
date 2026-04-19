"""
REST API routes for Purchase management.

Provides endpoints for listing, retrieving, and confirming purchases.
Purchase confirmation triggers atomic stock updates via the inventory
movement engine (purchase_service → inventory_service).

RBAC:
  - OWNER + MANAGER: full access
  - SALES: read-only
"""

from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from app.db.deps import check_role, get_uow
from app.models.enums import UserRole
from app.models.user import User
from app.repositories.base import AbstractUnitOfWork
from app.schemas.purchase import PurchaseCreate, PurchaseResponse
from app.services import purchase_service

router = APIRouter(prefix="/purchases", tags=["Purchases"])


@router.post("/", response_model=PurchaseResponse, status_code=201)
def create_purchase(
    purchase_data: PurchaseCreate,
    current_user: User = Depends(check_role([UserRole.OWNER, UserRole.MANAGER])),
    uow: AbstractUnitOfWork = Depends(get_uow),
):
    """
    Create a new purchase record in 'pending' status.

    Confirmation (PATCH /{id}/confirm) is required to update stock.

    RBAC: OWNER, MANAGER.

    Args:
        purchase_data (PurchaseCreate): Validated purchase payload.
        current_user (User): Authenticated user.
        uow (AbstractUnitOfWork): Unit of Work.

    Returns:
        PurchaseResponse: The created purchase record.
    """
    return purchase_service.create_purchase(uow, purchase_data)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/", response_model=List[PurchaseResponse])
def list_purchases(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(
        check_role([UserRole.OWNER, UserRole.MANAGER, UserRole.SALES])
    ),
    uow: AbstractUnitOfWork = Depends(get_uow),
):
    """
    List all purchases with pagination.

    RBAC: OWNER, MANAGER, SALES (read-only).

    Args:
        limit (int): Maximum records to return.
        offset (int): Records to skip.
        current_user (User): Authenticated user.
        uow (AbstractUnitOfWork): Unit of Work.

    Returns:
        List[PurchaseResponse]: Purchases with their line items.
    """
    return purchase_service.list_purchases(uow, limit=limit, offset=offset)


@router.get("/{purchase_id}", response_model=PurchaseResponse)
def get_purchase(
    purchase_id: UUID,
    current_user: User = Depends(
        check_role([UserRole.OWNER, UserRole.MANAGER, UserRole.SALES])
    ),
    uow: AbstractUnitOfWork = Depends(get_uow),
):
    """
    Retrieve a single purchase by ID with line items.

    RBAC: OWNER, MANAGER, SALES.

    Args:
        purchase_id (UUID): Unique identifier of the purchase.
        current_user (User): Authenticated user.
        uow (AbstractUnitOfWork): Unit of Work.

    Returns:
        PurchaseResponse: Purchase with line items.

    Raises:
        HTTPException 404: If purchase not found.
    """
    purchase = purchase_service.get_purchase(uow, purchase_id)
    if not purchase:
        raise HTTPException(status_code=404, detail="Purchase not found")
    return purchase


@router.patch("/{purchase_id}/confirm", response_model=PurchaseResponse)
def confirm_purchase(
    purchase_id: UUID,
    current_user: User = Depends(check_role([UserRole.OWNER, UserRole.MANAGER])),
    uow: AbstractUnitOfWork = Depends(get_uow),
):
    """
    Confirm a purchase and update raw material stock.

    For each purchase line item with a raw_material_id, the corresponding
    raw material's stock is incremented and an inventory_movement record
    ('raw_in') is created for a full audit trail.

    For GST purchases (tax_invoice + is_itc_eligible=True), an ITC entry
    is logged for future accounting reconciliation.

    RBAC: OWNER, MANAGER.

    Args:
        purchase_id (UUID): Unique identifier of the purchase to confirm.
        current_user (User): Authenticated user.
        uow (AbstractUnitOfWork): Unit of Work.

    Returns:
        PurchaseResponse: Purchase with status='confirmed'.

    Raises:
        HTTPException 404: If purchase not found.
        HTTPException 400: If already confirmed or cancelled.
    """
    return purchase_service.confirm_purchase(
        uow, purchase_id, confirmed_by=current_user.id
    )
