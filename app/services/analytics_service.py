from sqlalchemy.orm import Session
from sqlalchemy import func, case
from datetime import date, datetime
from typing import List, Optional
from decimal import Decimal
from uuid import UUID

from app.models.sale import Sale
from app.models.sale_item import SaleItem
from app.models.item import Item
from app.schemas.report import (
    SalesReportResponse, 
    TopProductResponse, 
    InventoryReportResponse,
    ProfitLossResponse,
    GSTSummaryResponse
)


def get_sales_analytics(db: Session, start_date: Optional[date] = None, end_date: Optional[date] = None) -> SalesReportResponse:
    query = db.query(
        func.count(Sale.id).label("total_orders"),
        func.sum(Sale.total_amount).label("total_revenue"),
        func.sum(Sale.tax_amount).label("total_tax"),
        func.sum(Sale.discount_amount).label("total_discount")
    )
    
    if start_date:
        query = query.filter(func.date(Sale.created_at) >= start_date)
    if end_date:
        query = query.filter(func.date(Sale.created_at) <= end_date)
        
    result = query.first()
    
    return SalesReportResponse(
        total_sales=int(result.total_revenue or 0), # Total value as primary metric
        total_orders=result.total_orders or 0,
        total_revenue=Decimal(str(result.total_revenue or 0)),
        total_tax=Decimal(str(result.total_tax or 0)),
        total_discount=Decimal(str(result.total_discount or 0))
    )


def get_top_products(db: Session, limit: int = 10) -> List[TopProductResponse]:
    results = db.query(
        Item.id,
        Item.name,
        Item.sku,
        func.sum(SaleItem.quantity).label("quantity_sold"),
        func.sum(SaleItem.total_price).label("revenue")
    ).join(SaleItem, Item.id == SaleItem.item_id)\
     .group_by(Item.id)\
     .order_by(func.sum(SaleItem.quantity).desc())\
     .limit(limit)\
     .all()
     
    return [
        TopProductResponse(
            item_id=r.id,
            name=r.name,
            sku=r.sku,
            quantity_sold=int(r.quantity_sold or 0),
            revenue=Decimal(str(r.revenue or 0))
        ) for r in results
    ]


def get_inventory_report(db: Session) -> List[InventoryReportResponse]:
    items = db.query(Item).all()
    return [
        InventoryReportResponse(
            item_id=item.id,
            name=item.name,
            sku=item.sku,
            current_stock=item.current_stock,
            min_stock=item.min_stock,
            is_low_stock=item.current_stock <= item.min_stock
        ) for item in items
    ]
