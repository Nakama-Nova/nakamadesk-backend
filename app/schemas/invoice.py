from datetime import datetime
from decimal import Decimal
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.schemas.customer import CustomerResponse


class InvoiceItemResponse(BaseModel):
    """
    Schema for an itemized line entry within an invoice response.
    """

    item_id: UUID
    item_name: str
    quantity: int
    price_at_sale: Decimal
    gst_percent: Decimal = Decimal("0.00")
    cgst_amount: Decimal = Decimal("0.00")
    sgst_amount: Decimal = Decimal("0.00")
    total_price: Decimal = Decimal("0.00")

    model_config = ConfigDict(from_attributes=True)


class InvoiceResponse(BaseModel):
    """
    Data Transfer Object for a complete invoice, including items and customer info.
    """

    id: UUID
    invoice_number: str
    invoice_date: datetime
    total_amount: Decimal
    customer: Optional[CustomerResponse] = None
    items: List[InvoiceItemResponse] = []

    model_config = ConfigDict(from_attributes=True)
