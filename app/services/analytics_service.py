from sqlalchemy.orm import Session
from sqlalchemy import func
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
        total_sales=int(result.total_revenue or 0),
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


def get_profit_loss(db: Session, start_date: Optional[date] = None, end_date: Optional[date] = None) -> ProfitLossResponse:
    # 1. Get Revenue (Total Sales)
    rev_query = db.query(func.sum(SaleItem.total_price))
    if start_date:
        rev_query = rev_query.join(Sale).filter(func.date(Sale.created_at) >= start_date)
    if end_date:
        rev_query = rev_query.join(Sale).filter(func.date(Sale.created_at) <= end_date)
    
    total_rev = rev_query.scalar() or 0.0
    
    # 2. Get Total Production Cost (Sum of individual item production_cost * quantity sold)
    cost_query = db.query(func.sum(SaleItem.quantity * Item.production_cost)).join(Item, SaleItem.item_id == Item.id)
    if start_date:
        cost_query = cost_query.join(Sale, SaleItem.sale_id == Sale.id).filter(func.date(Sale.created_at) >= start_date)
    if end_date:
        cost_query = cost_query.join(Sale, SaleItem.sale_id == Sale.id).filter(func.date(Sale.created_at) <= end_date)
        
    total_cost = cost_query.scalar() or 0.0
    
    revenue = Decimal(str(total_rev))
    cost = Decimal(str(total_cost))
    net_profit = revenue - cost
    
    margin_pct = (net_profit / revenue * 100).quantize(Decimal("0.01")) if revenue > 0 else Decimal("0.00")
    
    return ProfitLossResponse(
        total_revenue=revenue,
        total_cost=cost,
        net_profit=net_profit,
        profit_margin_pct=margin_pct,
        alert_flag=margin_pct < Decimal("10") if revenue > 0 else False
    )


def get_gst_summary(db: Session, start_date: Optional[date] = None, end_date: Optional[date] = None) -> GSTSummaryResponse:
    query = db.query(
        func.sum(SaleItem.total_price).label("gross"),
        func.sum(SaleItem.cgst_amount).label("cgst"),
        func.sum(SaleItem.sgst_amount).label("sgst")
    )
    
    if start_date:
        query = query.join(Sale).filter(func.date(Sale.created_at) >= start_date)
    if end_date:
        query = query.join(Sale).filter(func.date(Sale.created_at) <= end_date)
        
    res = query.first()
    
    gross = Decimal(str(res.gross or 0))
    cgst = Decimal(str(res.cgst or 0))
    sgst = Decimal(str(res.sgst or 0))
    igst = Decimal("0.00")
    
    total_tax = cgst + sgst + igst
    taxable_value = gross - total_tax
    
    return GSTSummaryResponse(
        taxable_value=taxable_value,
        cgst_total=cgst,
        sgst_total=sgst,
        igst_total=igst,
        total_tax=total_tax
    )
