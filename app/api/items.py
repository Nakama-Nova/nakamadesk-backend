from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from app.db.deps import get_uow, check_role
from app.repositories.base import AbstractUnitOfWork
from app.models.enums import UserRole
from app.models.user import User
from app.schemas.item import ItemCreate, ItemResponse, ItemUpdate
from app.schemas.stock import StockUpdate
from app.services import inventory_service

router = APIRouter(prefix="/items", tags=["Items"])


@router.post("/", response_model=ItemResponse)
def create_item(
    item: ItemCreate,
    current_user: User = Depends(check_role([UserRole.OWNER, UserRole.MANAGER])),
    uow: AbstractUnitOfWork = Depends(get_uow),
):
    """
    Create a new inventory item.

    RBAC: Restricted to OWNER and MANAGER roles.

    Args:
        item (ItemCreate): Item data to create.
        current_user (User): Authenticated user.
        uow (AbstractUnitOfWork): Unit of Work for database operations.

    Returns:
        ItemResponse: The created item.
    """
    return inventory_service.create_item(uow, item)


@router.get("/", response_model=List[ItemResponse])
def get_items(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(check_role([UserRole.OWNER, UserRole.MANAGER])),
    uow: AbstractUnitOfWork = Depends(get_uow),
):
    """
    List all inventory items with pagination.

    RBAC: Restricted to OWNER and MANAGER roles.

    Args:
        limit (int): Maximum number of items to return.
        offset (int): Number of items to skip.
        current_user (User): Authenticated user.
        uow (AbstractUnitOfWork): Unit of Work for database operations.

    Returns:
        List[ItemResponse]: List of inventory items.
    """
    return inventory_service.get_items(uow, limit, offset)


@router.get("/{item_id}", response_model=ItemResponse)
def get_item(
    item_id: UUID,
    current_user: User = Depends(check_role([UserRole.OWNER, UserRole.MANAGER])),
    uow: AbstractUnitOfWork = Depends(get_uow),
):
    """
    Retrieve details of a specific inventory item by ID.

    RBAC: Restricted to OWNER and MANAGER roles.

    Args:
        item_id (UUID): Unique ID of the item.
        current_user (User): Authenticated user.
        uow (AbstractUnitOfWork): Unit of Work for database operations.

    Returns:
        ItemResponse: The requested item.

    Raises:
        HTTPException: If item is not found.
    """
    item = inventory_service.get_item(uow, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    return item


@router.put("/{item_id}", response_model=ItemResponse)
def update_item(
    item_id: UUID,
    item_data: ItemUpdate,
    current_user: User = Depends(check_role([UserRole.OWNER, UserRole.MANAGER])),
    uow: AbstractUnitOfWork = Depends(get_uow),
):
    """
    Update an existing inventory item.

    RBAC: Restricted to OWNER and MANAGER roles.

    Args:
        item_id (UUID): Unique ID of the item.
        item_data (ItemUpdate): Updated item data.
        current_user (User): Authenticated user.
        uow (AbstractUnitOfWork): Unit of Work for database operations.

    Returns:
        ItemResponse: The updated item.

    Raises:
        HTTPException: If item is not found.
    """
    item = inventory_service.update_item(uow, item_id, item_data)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    return item


@router.delete("/{item_id}")
def delete_item(
    item_id: UUID,
    current_user: User = Depends(check_role([UserRole.OWNER, UserRole.MANAGER])),
    uow: AbstractUnitOfWork = Depends(get_uow),
):
    """
    Delete an inventory item.

    RBAC: Restricted to OWNER and MANAGER roles.

    Args:
        item_id (UUID): Unique ID of the item to delete.
        current_user (User): Authenticated user.
        uow (AbstractUnitOfWork): Unit of Work for database operations.

    Returns:
        dict: Success message.

    Raises:
        HTTPException: If item is not found.
    """
    success = inventory_service.delete_item(uow, item_id)
    if not success:
        raise HTTPException(status_code=404, detail="Item not found")
    return {"message": "Item deleted successfully"}


@router.patch("/{item_id}/stock", response_model=ItemResponse)
def adjust_stock(
    item_id: UUID,
    stock_update: StockUpdate,
    current_user: User = Depends(check_role([UserRole.OWNER, UserRole.MANAGER])),
    uow: AbstractUnitOfWork = Depends(get_uow),
):
    """
    Adjust the stock level of an existing inventory item.

    RBAC: Restricted to OWNER and MANAGER roles.

    Args:
        item_id (UUID): Unique ID of the item.
        stock_update (StockUpdate): Stock adjustment details.
        current_user (User): Authenticated user.
        uow (AbstractUnitOfWork): Unit of Work for database operations.

    Returns:
        ItemResponse: The updated item with new stock level.
    """
    return inventory_service.update_item_stock(uow, item_id, stock_update)
