from pydantic import BaseModel


class StockUpdate(BaseModel):
    """
    Schema for manual inventory stock adjustments.
    """

    quantity: int
