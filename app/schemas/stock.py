from typing import Optional

from pydantic import BaseModel


class StockUpdate(BaseModel):
    """
    Schema for manual inventory stock adjustments.
    """

    quantity: int
    notes: Optional[str] = None
