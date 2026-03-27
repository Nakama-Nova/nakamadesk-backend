from datetime import date, datetime
from typing import List, Optional
from decimal import Decimal
import pandas as pd
from io import BytesIO
from sqlalchemy import func
from sqlalchemy.orm import joinedload

from app.repositories.base import AbstractUnitOfWork
from app.models.sale import Sale
from app.models.sale_item import SaleItem
from app.models.item import Item
from app.schemas.report import (
    SalesReportResponse,
    TopProductResponse,
    InventoryReportResponse,
    ProfitLossResponse,
    GSTSummaryResponse,
)


def get_sales_analytics(
    uow: AbstractUnitOfWork,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
) -> SalesReportResponse:
    """
    Calculate high-level sales metrics for a given date range.

    Args:
        uow (AbstractUnitOfWork): Unit of Work for database access.
        start_date (Optional[date]): Filter from this date inclusive.
        end_date (Optional[date]): Filter to this date inclusive.

    Returns:
        SalesReportResponse: Aggregated metrics including total orders, revenue, and tax.
    """
    query = uow.session.query(
        func.count(Sale.id).label("total_orders"),
        func.sum(Sale.total_amount).label("total_revenue"),
        func.sum(Sale.tax_total).label("total_tax"),
        func.sum(Sale.discount).label("total_discount"),
    )

    if start_date:
        query = query.filter(
            Sale.created_at >= datetime.combine(start_date, datetime.min.time())
        )
    if end_date:
        query = query.filter(
            Sale.created_at <= datetime.combine(end_date, datetime.max.time())
        )

    result = query.first()

    revenue = Decimal(str(result.total_revenue or 0))
    tax = Decimal(str(result.total_tax or 0))
    discount = Decimal(str(result.total_discount or 0))

    return SalesReportResponse(
        total_sales=revenue,
        total_orders=result.total_orders or 0,
        total_revenue=revenue,
        total_tax=tax,
        total_discount=discount,
    )


def get_top_products(
    uow: AbstractUnitOfWork, limit: int = 10
) -> List[TopProductResponse]:
    """
    Retrieve a list of the most popular products based on quantity sold.

    Args:
        uow (AbstractUnitOfWork): Unit of Work for database access.
        limit (int): Maximum number of top products to return.

    Returns:
        List[TopProductResponse]: Ranked list of products with sales quantity and revenue.
    """
    results = (
        uow.session.query(
            Item.id,
            Item.name,
            Item.sku,
            func.sum(SaleItem.quantity).label("quantity_sold"),
            func.sum(SaleItem.total_price).label("revenue"),
        )
        .join(SaleItem, Item.id == SaleItem.item_id)
        .group_by(Item.id)
        .order_by(func.sum(SaleItem.quantity).desc())
        .limit(limit)
        .all()
    )

    return [
        TopProductResponse(
            item_id=r.id,
            name=r.name,
            sku=r.sku,
            quantity_sold=int(r.quantity_sold or 0),
            revenue=Decimal(str(r.revenue or 0)),
        )
        for r in results
    ]


def get_inventory_report(uow: AbstractUnitOfWork) -> List[InventoryReportResponse]:
    """
    Generate a report detailing current stock levels for all inventory items.

    Args:
        uow (AbstractUnitOfWork): Unit of Work for database access.

    Returns:
        List[InventoryReportResponse]: Current stock status, including low-stock alerts.
    """
    items = uow.session.query(
        Item.id, Item.name, Item.sku, Item.current_stock, Item.min_stock
    ).all()
    return [
        InventoryReportResponse(
            item_id=item.id,
            name=item.name,
            sku=item.sku,
            current_stock=int(item.current_stock or 0),
            min_stock=int(item.min_stock or 0),
            is_low_stock=(item.current_stock or 0) <= (item.min_stock or 0),
        )
        for item in items
    ]


def get_profit_loss(
    uow: AbstractUnitOfWork,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
) -> ProfitLossResponse:
    """
    Perform a Profit and Loss analysis for a specific timeframe.

    Calculates revenue from sales and estimates cost based on item production costs.

    Args:
        uow (AbstractUnitOfWork): Unit of Work for database access.
        start_date (Optional[date]): Analysis start date.
        end_date (Optional[date]): Analysis end date.

    Returns:
        ProfitLossResponse: P&L summary including margin and performance alerts.
    """
    # 1. Get Revenue (Total Sales)
    rev_query = uow.session.query(func.sum(SaleItem.total_price))
    if start_date or end_date:
        rev_query = rev_query.join(Sale)
    if start_date:
        rev_query = rev_query.filter(
            Sale.created_at >= datetime.combine(start_date, datetime.min.time())
        )
    if end_date:
        rev_query = rev_query.filter(
            Sale.created_at <= datetime.combine(end_date, datetime.max.time())
        )

    total_rev = rev_query.scalar() or 0.0

    # 2. Get Total Production Cost (Sum of individual item production_cost * quantity sold)
    cost_query = uow.session.query(
        func.sum(SaleItem.quantity * Item.production_cost)
    ).join(Item, SaleItem.item_id == Item.id)
    if start_date or end_date:
        cost_query = cost_query.join(Sale, SaleItem.sale_id == Sale.id)
    if start_date:
        cost_query = cost_query.filter(
            Sale.created_at >= datetime.combine(start_date, datetime.min.time())
        )
    if end_date:
        cost_query = cost_query.filter(
            Sale.created_at <= datetime.combine(end_date, datetime.max.time())
        )

    total_cost = cost_query.scalar() or 0.0

    revenue = Decimal(str(total_rev))
    cost = Decimal(str(total_cost))
    net_profit = revenue - cost

    margin_pct = (
        (net_profit / revenue * 100).quantize(Decimal("0.01"))
        if revenue > 0
        else Decimal("0.00")
    )

    return ProfitLossResponse(
        total_revenue=revenue,
        total_cost=cost,
        net_profit=net_profit,
        profit_margin_pct=margin_pct,
        alert_flag=margin_pct < Decimal("10") if revenue > 0 else False,
    )


def get_gst_summary(
    uow: AbstractUnitOfWork,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
) -> GSTSummaryResponse:
    """
    Summarize GST (Goods and Services Tax) data for a given period.

    Breakdown includes CGST, SGST, and total taxable value.

    Args:
        uow (AbstractUnitOfWork): Unit of Work for database access.
        start_date (Optional[date]): Summary start date.
        end_date (Optional[date]): Summary end date.

    Returns:
        GSTSummaryResponse: Aggregated tax data for reporting.
    """
    query = uow.session.query(
        func.sum(SaleItem.total_price).label("gross"),
        func.sum(SaleItem.cgst_amount).label("cgst"),
        func.sum(SaleItem.sgst_amount).label("sgst"),
    )

    if start_date:
        query = query.join(Sale).filter(
            Sale.created_at >= datetime.combine(start_date, datetime.min.time())
        )
    if end_date:
        if not start_date:  # joining Sale if not already joined
            query = query.join(Sale)
        query = query.filter(
            Sale.created_at <= datetime.combine(end_date, datetime.max.time())
        )

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
        total_tax=total_tax,
    )


def export_sales_to_excel(
    uow: AbstractUnitOfWork,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
) -> BytesIO:
    """
    Export detailed sales records to an Excel spreadsheet.

    Args:
        uow (AbstractUnitOfWork): Unit of Work for database access.
        start_date (Optional[date]): Export start date.
        end_date (Optional[date]): Export end date.

    Returns:
        BytesIO: In-memory Excel file stream.
    """
    query = uow.session.query(Sale).options(joinedload(Sale.customer))
    if start_date:
        query = query.filter(func.date(Sale.created_at) >= start_date)
    if end_date:
        query = query.filter(func.date(Sale.created_at) <= end_date)

    sales = query.all()

    data = []
    for s in sales:
        data.append(
            {
                "Invoice No": s.invoice_number,
                "Date": s.created_at.strftime("%Y-%m-%d"),
                "Customer": s.customer.name if s.customer else "Walk-in",
                "Taxable Value": float(s.total_amount - s.tax_total),
                "CGST": float(s.tax_total / 2),
                "SGST": float(s.tax_total / 2),
                "Total Amount": float(s.total_amount),
            }
        )

    df = pd.DataFrame(data)
    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Sales Report")

    output.seek(0)
    return output
