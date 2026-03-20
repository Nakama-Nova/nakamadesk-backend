from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import datetime, date

from app.db.deps import get_db
from app.db.deps import get_current_user
from app.models.sale import Sale
from app.models.sale_item import SaleItem
from app.models.item import Item
from app.models.user import User

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])

@router.get("/summary")
def get_dashboard_summary(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    today = date.today()
    
    # Today's sales count
    today_sales = db.query(func.count(Sale.id)).filter(
        func.date(Sale.created_at) == today
    ).scalar() or 0
    
    # Today's revenue
    today_revenue = db.query(func.sum(Sale.total_amount)).filter(
        func.date(Sale.created_at) == today
    ).scalar() or 0.0
    
    # Items sold today (sum of quantities)
    items_sold = db.query(func.sum(SaleItem.quantity)).join(Sale).filter(
        func.date(Sale.created_at) == today
    ).scalar() or 0
    
    # Low stock count (items with current_stock <= min_stock)
    low_stock_count = db.query(func.count(Item.id)).filter(
        Item.current_stock <= Item.min_stock
    ).scalar() or 0
    
    return {
        "today_sales": today_sales,
        "today_revenue": today_revenue,
        "items_sold": items_sold,
        "low_stock_count": low_stock_count
    }
