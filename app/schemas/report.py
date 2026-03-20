from pydantic import BaseModel
from uuid import UUID
from decimal import Decimal


class SalesReportResponse(BaseModel):
    total_sales: int
    total_orders: int
    total_revenue: Decimal
    total_tax: Decimal
    total_discount: Decimal


class TopProductResponse(BaseModel):
    item_id: UUID
    name: str
    sku: str
    quantity_sold: int
    revenue: Decimal


class InventoryReportResponse(BaseModel):
    item_id: UUID
    name: str
    sku: str
    current_stock: int
    min_stock: int
    is_low_stock: bool


class ProfitLossResponse(BaseModel):
    total_revenue: Decimal
    total_cost: Decimal
    net_profit: Decimal
    profit_margin_pct: Decimal
    alert_flag: bool = False


class GSTSummaryResponse(BaseModel):
    taxable_value: Decimal
    cgst_total: Decimal
    sgst_total: Decimal
    igst_total: Decimal
    total_tax: Decimal
