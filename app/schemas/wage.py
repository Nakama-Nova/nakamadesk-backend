from pydantic import BaseModel, ConfigDict
from uuid import UUID
from datetime import datetime
from typing import Optional, List
from decimal import Decimal


class WageBase(BaseModel):
    """
    Base attributes for daily wage records.
    """

    attendance_id: UUID
    amount: Decimal
    payment_status: str = "pending"


class WageResponse(WageBase):
    """
    Data Transfer Object for detailed wage records in API responses.
    """

    id: UUID
    transaction_reference: Optional[str] = None
    paid_at: Optional[datetime] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class WagePaymentRequest(BaseModel):
    """
    Request schema for processing multiple wage payments simultaneously.
    """

    attendance_ids: List[UUID]
    transaction_reference: Optional[str] = None
