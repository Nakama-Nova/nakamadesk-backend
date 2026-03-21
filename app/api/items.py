from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db.deps import get_db, check_role
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
    db: Session = Depends(get_db),
):
    return inventory_service.create_item(db, item)


@router.get("/", response_model=List[ItemResponse])
def get_items(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(check_role([UserRole.OWNER, UserRole.MANAGER])),
    db: Session = Depends(get_db),
):
    return inventory_service.get_items(db, limit, offset)


@router.get("/{item_id}", response_model=ItemResponse)
def get_item(
    item_id: UUID,
    current_user: User = Depends(check_role([UserRole.OWNER, UserRole.MANAGER])),
    db: Session = Depends(get_db),
):
    item = inventory_service.get_item(db, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    return item


@router.put("/{item_id}", response_model=ItemResponse)
def update_item(
    item_id: UUID,
    item_data: ItemUpdate,
    current_user: User = Depends(check_role([UserRole.OWNER, UserRole.MANAGER])),
    db: Session = Depends(get_db),
):
    item = inventory_service.update_item(db, item_id, item_data)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    return item


@router.delete("/{item_id}")
def delete_item(
    item_id: UUID,
    current_user: User = Depends(check_role([UserRole.OWNER, UserRole.MANAGER])),
    db: Session = Depends(get_db),
):
    success = inventory_service.delete_item(db, item_id)
    if not success:
        raise HTTPException(status_code=404, detail="Item not found")
    return {"message": "Item deleted successfully"}


@router.patch("/{item_id}/stock", response_model=ItemResponse)
def adjust_stock(
    item_id: UUID,
    stock_update: StockUpdate,
    current_user: User = Depends(check_role([UserRole.OWNER, UserRole.MANAGER])),
    db: Session = Depends(get_db),
):
    return inventory_service.update_item_stock(db, item_id, stock_update)
