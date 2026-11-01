from typing import Optional
from uuid import UUID

from pydantic import BaseModel


class StockUpdate(BaseModel):
    """
    Schema for manual inventory stock adjustments.
    """

    quantity: int
    notes: Optional[str] = None
    location_id: Optional[UUID] = None  # the unit the count or change applies to
