from pydantic import BaseModel


class StockUpdate(BaseModel):
    quantity: int
