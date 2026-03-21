from pydantic import BaseModel, ConfigDict
from uuid import UUID
from datetime import datetime
from typing import Optional, List
from decimal import Decimal


class WageBase(BaseModel):
    attendance_id: UUID
    amount: Decimal
    payment_status: str = "pending"


class WageResponse(WageBase):
    id: UUID
    transaction_reference: Optional[str] = None
    paid_at: Optional[datetime] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class WagePaymentRequest(BaseModel):
    attendance_ids: List[UUID]
    transaction_reference: Optional[str] = None
