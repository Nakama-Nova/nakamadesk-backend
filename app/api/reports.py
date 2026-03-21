from datetime import date
from typing import List, Optional
from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.db.deps import get_db, check_role
from app.models.enums import UserRole
from app.models.user import User
from app.schemas.report import (
    SalesReportResponse,
    TopProductResponse,
    InventoryReportResponse,
    ProfitLossResponse,
    GSTSummaryResponse,
)
from app.services import analytics_service

router = APIRouter(prefix="/reports", tags=["Reports"])


@router.get("/sales", response_model=SalesReportResponse)
def get_sales_report(
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    current_user: User = Depends(check_role([UserRole.OWNER])),
    db: Session = Depends(get_db),
):
    return analytics_service.get_sales_analytics(db, start_date, end_date)


@router.get("/top-products", response_model=List[TopProductResponse])
def get_top_products_report(
    limit: int = Query(10),
    current_user: User = Depends(check_role([UserRole.OWNER])),
    db: Session = Depends(get_db),
):
    return analytics_service.get_top_products(db, limit)


@router.get("/inventory", response_model=List[InventoryReportResponse])
def get_inventory_report(
    current_user: User = Depends(check_role([UserRole.OWNER])),
    db: Session = Depends(get_db),
):
    return analytics_service.get_inventory_report(db)


@router.get("/profit-loss", response_model=ProfitLossResponse)
def get_profit_loss_analytics(
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    current_user: User = Depends(check_role([UserRole.OWNER])),
    db: Session = Depends(get_db),
):
    return analytics_service.get_profit_loss(db, start_date, end_date)


@router.get("/gst-summary", response_model=GSTSummaryResponse)
def get_gst_summary_report(
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    current_user: User = Depends(check_role([UserRole.OWNER])),
    db: Session = Depends(get_db),
):
    return analytics_service.get_gst_summary(db, start_date, end_date)


@router.get("/export/sales")
def export_sales_excel(
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    current_user: User = Depends(check_role([UserRole.OWNER])),
    db: Session = Depends(get_db),
):
    excel_file = analytics_service.export_sales_to_excel(db, start_date, end_date)
    headers = {"Content-Disposition": 'attachment; filename="sales_report.xlsx"'}
    return StreamingResponse(
        excel_file,
        headers=headers,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
