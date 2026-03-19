from datetime import datetime
from typing import List, Optional
from uuid import UUID
from pydantic import BaseModel, ConfigDict

from app.schemas.customer import CustomerResponse


class InvoiceItemResponse(BaseModel):
    item_id: UUID
    item_name: str
    quantity: int
    price_at_sale: float
    gst_percent: float = 0.0
    cgst_amount: float = 0.0
    sgst_amount: float = 0.0
    total_price: float = 0.0

    model_config = ConfigDict(from_attributes=True)


class InvoiceResponse(BaseModel):
    id: UUID
    invoice_number: str
    invoice_date: datetime
    total_amount: float
    customer: Optional[CustomerResponse] = None
    items: List[InvoiceItemResponse] = []

    model_config = ConfigDict(from_attributes=True)
