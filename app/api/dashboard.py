from datetime import date

from fastapi import APIRouter, Depends
from sqlalchemy import case, func

from app.db.deps import check_role, get_uow
from app.models.attendance import Attendance
from app.models.enums import UserRole
from app.models.item import Item
from app.models.user import User
from app.repositories.base import AbstractUnitOfWork
from app.services import analytics_service

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])


@router.get("/summary")
def get_dashboard_summary(
    current_user: User = Depends(check_role([UserRole.OWNER, UserRole.MANAGER])),
    uow: AbstractUnitOfWork = Depends(get_uow),
):
    """
    Retrieve key analytics summary for the dashboard.

    Includes today's sales count, revenue, low stock count, pending wages, and top products.

    RBAC: Restricted to OWNER and MANAGER roles.

    Args:
        current_user (User): Authenticated user.
        uow (AbstractUnitOfWork): Unit of Work for database operations.

    Returns:
        dict: Summary statistics for the dashboard.
    """
    today = date.today()

    sales = analytics_service.get_sales_analytics(uow, today, today)

    # Direct scalar extraction
    low_stock_count = (
        uow.session.query(func.count(Item.id))
        .filter(Item.current_stock <= Item.min_stock)
        .scalar()
        or 0
    )

    top_products = analytics_service.get_top_products(uow, limit=5)

    # Aggregate pending wages total
    pending_wages_total = (
        uow.session.query(
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
