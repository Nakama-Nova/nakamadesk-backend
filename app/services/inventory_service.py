from typing import List, Optional
from uuid import UUID
from fastapi import HTTPException
from sqlalchemy.orm import Session
from app.models.item import Item
from app.schemas.item import ItemCreate, ItemUpdate
from app.schemas.stock import StockUpdate

import logging

logger = logging.getLogger(__name__)


def get_items(db: Session, limit: int = 50, offset: int = 0) -> List[Item]:
    """Retrieve items with pagination."""
    return db.query(Item).limit(limit).offset(offset).all()


def get_item(db: Session, item_id: UUID) -> Optional[Item]:
    """Retrieve a single item by ID."""
    return db.query(Item).filter(Item.id == item_id).first()


def create_item(db: Session, item_data: ItemCreate) -> Item:
    """Create a new inventory item."""
    new_item = Item(**item_data.model_dump())
    db.add(new_item)
    db.commit()
    db.refresh(new_item)
    logger.info(f"Item created: {new_item.sku} (ID: {new_item.id})")
    return new_item


def update_item(db: Session, item_id: UUID, item_data: ItemUpdate) -> Optional[Item]:
    """Update an existing item."""
    item = get_item(db, item_id)
    if not item:
        return None

    update_dict = item_data.model_dump(exclude_unset=True)
    for key, value in update_dict.items():
        setattr(item, key, value)

    db.commit()
    db.refresh(item)
    logger.info(f"Item updated: {item.sku} (ID: {item.id})")
    return item


def delete_item(db: Session, item_id: UUID) -> bool:
    """Delete an item."""
    item = get_item(db, item_id)
    if not item:
        return False

    db.delete(item)
    db.commit()
    logger.info(f"Item deleted: {item_id}")
    return True


def update_item_stock(db: Session, item_id: UUID, stock_update: StockUpdate) -> Item:
    """Adjust stock quantity for a single item."""
    item = get_item(db, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")

    new_stock = (item.current_stock or 0) + stock_update.quantity
    if new_stock < 0:
        raise HTTPException(status_code=400, detail="Stock cannot go below zero")

    item.current_stock = new_stock
    db.commit()
    db.refresh(item)

    logger.info(f"stock updated: item {item.id} new stock {new_stock}")
    return item
