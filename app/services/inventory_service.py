from typing import List, Optional
from uuid import UUID
from fastapi import HTTPException
from app.repositories.base import AbstractUnitOfWork
from app.models.item import Item
from app.schemas.item import ItemCreate, ItemUpdate
from app.schemas.stock import StockUpdate

import logging

logger = logging.getLogger(__name__)


def get_items(uow: AbstractUnitOfWork, limit: int = 50, offset: int = 0) -> List[Item]:
    """
    Retrieve a list of inventory items with pagination.

    Args:
        uow (AbstractUnitOfWork): Unit of Work for database access.
        limit (int): Maximum number of items to return.
        offset (int): Number of records to skip.

    Returns:
        List[Item]: List of item records.
    """
    return uow.items.session.query(Item).limit(limit).offset(offset).all()


def get_item(uow: AbstractUnitOfWork, item_id: UUID) -> Optional[Item]:
    """
    Retrieve a single inventory item by its unique ID.

    Args:
        uow (AbstractUnitOfWork): Unit of Work for database access.
        item_id (UUID): Unique ID of the item.

    Returns:
        Optional[Item]: The item record if found, else None.
    """
    return uow.items.get_by_id(item_id)


def create_item(uow: AbstractUnitOfWork, item_data: ItemCreate) -> Item:
    """
    Create a new inventory item and persist it to the database.

    Args:
        uow (AbstractUnitOfWork): Unit of Work for database operations.
        item_data (ItemCreate): Payload containing item details (SKU, name, etc.).

    Returns:
        Item: The newly created item object.
    """
    new_item = Item(**item_data.model_dump())
    with uow:
        uow.items.add(new_item)
        uow.commit()
    uow.refresh(new_item)
    logger.info(f"Item created: {new_item.sku} (ID: {new_item.id})")
    return new_item


def update_item(
    uow: AbstractUnitOfWork, item_id: UUID, item_data: ItemUpdate
) -> Optional[Item]:
    """
    Update an existing inventory item's attributes.

    Args:
        uow (AbstractUnitOfWork): Unit of Work for database operations.
        item_id (UUID): Unique ID of the item to update.
        item_data (ItemUpdate): Updated data fields.

    Returns:
        Optional[Item]: The updated item record, or None if not found.
    """
    with uow:
        item = uow.items.get_by_id(item_id)
        if not item:
            return None

        update_dict = item_data.model_dump(exclude_unset=True)
        for key, value in update_dict.items():
            setattr(item, key, value)

        uow.commit()
    uow.refresh(item)
    logger.info(f"Item updated: {item.sku} (ID: {item.id})")
    return item


def delete_item(uow: AbstractUnitOfWork, item_id: UUID) -> bool:
    """
    Delete an inventory item from the system.

    Args:
        uow (AbstractUnitOfWork): Unit of Work for database operations.
        item_id (UUID): Unique ID of the item to delete.

    Returns:
        bool: True if deleted successfully, False if not found.
    """
    with uow:
        item = uow.items.get_by_id(item_id)
        if not item:
            return False

        uow.items.delete(item)
        uow.commit()
    logger.info(f"Item deleted: {item_id}")
    return True


def update_item_stock(
    uow: AbstractUnitOfWork, item_id: UUID, stock_update: StockUpdate
) -> Item:
    """
    Adjust the current stock level for an inventory item.

    Supports positive (addition) and negative (deduction) adjustments.
    Ensures that stock level does not fall below zero.

    Args:
        uow (AbstractUnitOfWork): Unit of Work for database operations.
        item_id (UUID): Unique ID of the item.
        stock_update (StockUpdate): Data containing the adjustment quantity.

    Returns:
        Item: The updated item with the new stock level.

    Raises:
        HTTPException: If the item is not found or resulting stock would be negative.
    """
    with uow:
        item = uow.items.get_by_id(item_id)
        if not item:
            raise HTTPException(status_code=404, detail="Item not found")

        new_stock = (item.current_stock or 0) + stock_update.quantity
        if new_stock < 0:
            raise HTTPException(status_code=400, detail="Stock cannot go below zero")

        item.current_stock = new_stock
        uow.commit()
    uow.refresh(item)

    logger.info(f"stock updated: item {item.id} new stock {new_stock}")
    return item
