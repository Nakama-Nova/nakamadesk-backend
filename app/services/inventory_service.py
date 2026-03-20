from uuid import UUID
from fastapi import HTTPException
from sqlalchemy.orm import Session
from app.models.item import Item
from app.schemas.stock import StockUpdate

import logging

logger = logging.getLogger(__name__)


def update_item_stock(db: Session, item_id: UUID, stock_update: StockUpdate) -> Item:
    """Adjust stock quantity for a single item."""
    item = db.query(Item).filter(Item.id == item_id).first()
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
