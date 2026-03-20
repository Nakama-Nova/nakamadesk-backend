from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from datetime import date

from app.db.deps import get_db, get_current_user
from app.models.user import User
from app.services import analytics_service, workforce_service

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])


@router.get("/summary")
def get_dashboard_summary(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    today = date.today()
    
    sales = analytics_service.get_sales_analytics(db, today, today)
    inventory = analytics_service.get_inventory_report(db)
    low_stock_count = sum(1 for item in inventory if item.is_low_stock)
    
    top_products = analytics_service.get_top_products(db, limit=5)
    
    # Get pending wages count/sum (from workforce service if available)
    pending_wages = workforce_service.get_pending_wages(db)
    pending_wages_total = sum(wage.total_amount for wage in pending_wages)
    
    return {
        "today_sales_count": sales.total_orders,
        "today_revenue": sales.total_revenue,
        "low_stock_count": low_stock_count,
        "pending_wages_total": pending_wages_total,
        "top_products": top_products
    }
