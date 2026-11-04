from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from app.db.deps import check_role, get_uow
from app.models.enums import UserRole
from app.models.user import User
from app.repositories.base import AbstractUnitOfWork
from app.schemas.sale import SaleCreate, SaleItemResponse, SaleResponse, SaleUpdate
from app.services.sales_service import (
    cancel_sale,
    create_sale_transaction,
    get_all_sales,
    get_sale_by_id,
    get_sale_items_by_id,
    update_sale,
)

router = APIRouter(prefix="/sales", tags=["Sales"])


@router.post("/", response_model=SaleResponse)
def create_sale(
    sale_data: SaleCreate,
    current_user: User = Depends(
        check_role([UserRole.OWNER, UserRole.MANAGER, UserRole.SALES])
    ),
    uow: AbstractUnitOfWork = Depends(get_uow),
):
    """
    Create a new sale transaction and update inventory levels.

    RBAC: Restricted to OWNER, MANAGER, and SALES roles.

    Args:
        sale_data (SaleCreate): Payload containing sale details and items.
        current_user (User): Authenticated user performing the action.
        uow (AbstractUnitOfWork): Unit of Work for database operations.

    Returns:
        SaleResponse: The created sale object.

    Raises:
        HTTPException: If stock is insufficient or validation fails.
    """
    return create_sale_transaction(uow, sale_data, current_user.id)


@router.get("/", response_model=List[SaleResponse])
def get_sales(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    customer_id: Optional[UUID] = None,
    date: str = None,
    current_user: User = Depends(
        check_role([UserRole.OWNER, UserRole.MANAGER, UserRole.SALES])
    ),
    uow: AbstractUnitOfWork = Depends(get_uow),
):
    """
    Retrieve a list of all sales with optional filtering and pagination.

    RBAC: Restricted to OWNER, MANAGER, and SALES roles.

    Args:
        limit (int): Maximum number of sales to return.
        offset (int): Number of sales to skip.
        customer_id (Optional[UUID]): Filter by a specific customer.
        date (str): Filter by a specific date (ISO format).
        current_user (User): Authenticated user.
        uow (AbstractUnitOfWork): Unit of Work for database operations.

    Returns:
        List[SaleResponse]: List of sales matching the criteria.
    """
    return get_all_sales(
        uow, limit=limit, offset=offset, customer_id=customer_id, date=date
    )


@router.get("/{sale_id}", response_model=SaleResponse)
def get_sale(
    sale_id: UUID,
    current_user: User = Depends(
        check_role([UserRole.OWNER, UserRole.MANAGER, UserRole.SALES])
    ),
    uow: AbstractUnitOfWork = Depends(get_uow),
):
    """
    Retrieve details of a specific sale by its unique ID.

    RBAC: Restricted to OWNER, MANAGER, and SALES roles.

    Args:
        sale_id (UUID): Unique ID of the sale.
        current_user (User): Authenticated user.
        uow (AbstractUnitOfWork): Unit of Work for database operations.

    Returns:
        SaleResponse: Detailed information about the sale.

    Raises:
        HTTPException: If the sale is not found.
    """
    sale = get_sale_by_id(uow, sale_id)
    if not sale:
        raise HTTPException(status_code=404, detail="Sale not found")
    return sale


@router.get("/{sale_id}/items", response_model=List[SaleItemResponse])
def get_sale_items(
    sale_id: UUID,
    current_user: User = Depends(
        check_role([UserRole.OWNER, UserRole.MANAGER, UserRole.SALES])
    ),
    uow: AbstractUnitOfWork = Depends(get_uow),
):
    """
    Retrieve all line items associated with a specific sale.

    RBAC: Restricted to OWNER, MANAGER, and SALES roles.

    Args:
        sale_id (UUID): Unique ID of the sale.
        current_user (User): Authenticated user.
        uow (AbstractUnitOfWork): Unit of Work for database operations.

    Returns:
        List[SaleItemResponse]: List of items in the sale.
    """
    return get_sale_items_by_id(uow, sale_id)


@router.patch("/{sale_id}", response_model=SaleResponse)
def edit_sale(
    sale_id: UUID,
    data: SaleUpdate,
    current_user: User = Depends(check_role([UserRole.OWNER, UserRole.MANAGER])),
    uow: AbstractUnitOfWork = Depends(get_uow),
):
    """
    Correct a bill's customer or payment details. What was sold and at what price is
    fixed: to change that, cancel the bill and make a new one. RBAC: OWNER, MANAGER.
    """
    return update_sale(uow, sale_id, data, current_user.id)


@router.delete("/{sale_id}", response_model=SaleResponse)
def delete_sale(
    sale_id: UUID,
    reason: str = Query(min_length=3, max_length=300),
    current_user: User = Depends(check_role([UserRole.OWNER])),
    uow: AbstractUnitOfWork = Depends(get_uow),
):
    """
    Delete a bill: the stock it took comes back (to the same location, with a stock
    movement for each piece), the bill stays in the history marked cancelled with the
    reason, the reports stop counting it, and the finance ledger reverses it on the
    next sync. A bill made from an order frees the order to be billed again.
    RBAC: OWNER only.
    """
    return cancel_sale(uow, sale_id, reason, current_user.id)
