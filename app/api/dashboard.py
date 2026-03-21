from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from datetime import date

from app.db.deps import get_db, check_role
from app.models.enums import UserRole
from app.models.user import User
from app.services import analytics_service

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])


from app.models.attendance import Attendance
from sqlalchemy import func, case

from app.models.item import Item


@router.get("/summary")
def get_dashboard_summary(
    current_user: User = Depends(check_role([UserRole.OWNER, UserRole.MANAGER])),
    db: Session = Depends(get_db),
):
    today = date.today()

    sales = analytics_service.get_sales_analytics(db, today, today)

    # Direct scalar extraction bypassing millions of Python list maps
    low_stock_count = (
        db.query(func.count(Item.id))
        .filter(Item.current_stock <= Item.min_stock)
        .scalar()
        or 0
    )

    top_products = analytics_service.get_top_products(db, limit=5)

    # Extract aggregate directly overriding missing property and avoiding object memory bloat
    pending_wages_total = (
        db.query(
            func.sum(
                case(
                    (Attendance.status == "half-day", Attendance.daily_wage / 2),
                    else_=Attendance.daily_wage,
                )
            )
        )
        .filter(
            Attendance.payment_status == "pending",
            Attendance.status.in_(["present", "half-day"]),
        )
        .scalar()
        or 0.0
    )

    return {
        "today_sales_count": sales.total_orders,
        "today_revenue": sales.total_revenue,
        "low_stock_count": low_stock_count,
        "pending_wages_total": pending_wages_total,
        "top_products": top_products,
    }
