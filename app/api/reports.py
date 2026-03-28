from datetime import date
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse

from app.db.deps import check_role, get_uow
from app.models.enums import UserRole
from app.models.user import User
from app.repositories.base import AbstractUnitOfWork
from app.schemas.report import (
    GSTSummaryResponse,
    InventoryReportResponse,
    ProfitLossResponse,
    SalesReportResponse,
    TopProductResponse,
)
from app.services import analytics_service

router = APIRouter(prefix="/reports", tags=["Reports"])


@router.get("/sales", response_model=SalesReportResponse)
def get_sales_report(
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    current_user: User = Depends(check_role([UserRole.OWNER, UserRole.MANAGER])),
    uow: AbstractUnitOfWork = Depends(get_uow),
):
    """
    Retrieve a comprehensive sales report for a specific date range.

    RBAC: Restricted to OWNER role.

    Args:
        start_date (Optional[date]): Start date for the report.
        end_date (Optional[date]): End date for the report.
        current_user (User): Authenticated user.
        uow (AbstractUnitOfWork): Unit of Work for database operations.

    Returns:
        SalesReportResponse: Aggregated sales analytics.
    """
    return analytics_service.get_sales_analytics(uow, start_date, end_date)


@router.get("/top-products", response_model=List[TopProductResponse])
def get_top_products_report(
    limit: int = Query(10),
    current_user: User = Depends(check_role([UserRole.OWNER, UserRole.MANAGER])),
    uow: AbstractUnitOfWork = Depends(get_uow),
):
    """
    Retrieve a report of top-selling products.

    RBAC: Restricted to OWNER role.

    Args:
        limit (int): Number of top products to return.
        current_user (User): Authenticated user.
        uow (AbstractUnitOfWork): Unit of Work for database operations.

    Returns:
        List[TopProductResponse]: List of top-selling products and their performance.
    """
    return analytics_service.get_top_products(uow, limit)


@router.get("/inventory", response_model=List[InventoryReportResponse])
def get_inventory_report(
    current_user: User = Depends(check_role([UserRole.OWNER, UserRole.MANAGER])),
    uow: AbstractUnitOfWork = Depends(get_uow),
):
    """
    Retrieve a complete inventory status report.

    RBAC: Restricted to OWNER role.

    Args:
        current_user (User): Authenticated user.
        uow (AbstractUnitOfWork): Unit of Work for database operations.

    Returns:
        List[InventoryReportResponse]: Current status of all items in inventory.
    """
    return analytics_service.get_inventory_report(uow)


@router.get("/profit-loss", response_model=ProfitLossResponse)
def get_profit_loss_analytics(
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    current_user: User = Depends(check_role([UserRole.OWNER, UserRole.MANAGER])),
    uow: AbstractUnitOfWork = Depends(get_uow),
):
    """
    Retrieve Profit and Loss (P&L) analytics for a specific date range.

    RBAC: Restricted to OWNER role.

    Args:
        start_date (Optional[date]): Start date for the analysis.
        end_date (Optional[date]): End date for the analysis.
        current_user (User): Authenticated user.
        uow (AbstractUnitOfWork): Unit of Work for database operations.

    Returns:
        ProfitLossResponse: P&L summary and breakdown.
    """
    return analytics_service.get_profit_loss(uow, start_date, end_date)


@router.get("/gst-summary", response_model=GSTSummaryResponse)
def get_gst_summary_report(
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    current_user: User = Depends(check_role([UserRole.OWNER, UserRole.MANAGER])),
    uow: AbstractUnitOfWork = Depends(get_uow),
):
    """
    Retrieve a GST summary report for a specific date range.

    RBAC: Restricted to OWNER role.

    Args:
        start_date (Optional[date]): Start date for the summary.
        end_date (Optional[date]): End date for the summary.
        current_user (User): Authenticated user.
        uow (AbstractUnitOfWork): Unit of Work for database operations.

    Returns:
        GSTSummaryResponse: Aggregated GST data.
    """
    return analytics_service.get_gst_summary(uow, start_date, end_date)


@router.get("/export/sales")
def export_sales_excel(
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    current_user: User = Depends(check_role([UserRole.OWNER, UserRole.MANAGER])),
    uow: AbstractUnitOfWork = Depends(get_uow),
):
    """
    Export sales data to an Excel file for a specific date range.

    RBAC: Restricted to OWNER role.

    Args:
        start_date (Optional[date]): Start date for the export.
        end_date (Optional[date]): End date for the export.
        current_user (User): Authenticated user.
        uow (AbstractUnitOfWork): Unit of Work for database operations.

    Returns:
        StreamingResponse: Excel file binary stream.
    """
    excel_file = analytics_service.export_sales_to_excel(uow, start_date, end_date)
    headers = {"Content-Disposition": 'attachment; filename="sales_report.xlsx"'}
    return StreamingResponse(
        excel_file,
        headers=headers,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
