from datetime import date, datetime
from typing import List

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db.deps import get_db, get_current_user
from app.models.item import Item
from app.models.sale import Sale
from app.models.sale_item import SaleItem
from app.models.user import User
from app.schemas.item import ItemResponse

router = APIRouter(prefix="/reports", tags=["Reports"])


@router.get("/sales/daily")
def get_daily_sales(
    current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    today = date.today()

    total_sales = (
        db.query(func.count(Sale.id))
        .filter(func.date(Sale.created_at) == today)
        .scalar()
    )

    total_revenue = (
        db.query(func.sum(Sale.total_amount))
        .filter(func.date(Sale.created_at) == today)
        .scalar()
    )

    items_sold = (
        db.query(func.sum(SaleItem.quantity))
        .join(Sale)
        .filter(func.date(Sale.created_at) == today)
        .scalar()
    )

    return {
        "total_sales": total_sales or 0,
        "items_sold": items_sold or 0,
        "total_revenue": total_revenue or 0,
    }


@router.get("/sales/summary")
def get_sales_summary(
    current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    total_sales = db.query(func.count(Sale.id)).scalar()

    total_revenue = db.query(func.sum(Sale.total_amount)).scalar()

    return {"total_sales": total_sales or 0, "total_revenue": total_revenue or 0}


@router.get("/inventory/low-stock", response_model=List[ItemResponse])
def get_low_stock_inventory(
    current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    low_stock_items = db.query(Item).filter(Item.current_stock <= Item.min_stock).all()

    return low_stock_items
