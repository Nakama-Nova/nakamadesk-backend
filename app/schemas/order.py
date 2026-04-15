"""
Pydantic schemas for the Order and OrderItem API endpoints.

Follows the same Base → Create → Update → Response pattern
used throughout the codebase.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

# ---------------------------------------------------------------------------
# Order Item schemas
# ---------------------------------------------------------------------------


class OrderItemCreate(BaseModel):
    """
    Schema for a single line item within a new order.

    Either item_id (standard catalogue item) or item_name
    (custom/bespoke item) must be provided, but not necessarily both.
    """

    item_id: Optional[UUID] = None
    item_name: Optional[str] = None
    quantity: int = Field(default=1, ge=1)
    unit_price: Decimal = Field(default=Decimal("0.00"), ge=0)
    notes: Optional[str] = None

    @model_validator(mode="after")
    def validate_item_identity(self) -> "OrderItemCreate":
        """Ensure at least one of item_id or item_name is provided."""
        if self.item_id is None and not self.item_name:
            raise ValueError(
                "Either item_id (standard) or item_name (custom) must be provided"
            )
        return self


class OrderItemResponse(BaseModel):
    """Data Transfer Object for a single order line item."""

    id: UUID
    order_id: UUID
    item_id: Optional[UUID] = None
    item_name: Optional[str] = None
    quantity: int
    unit_price: Decimal
    notes: Optional[str] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------------------
# Order schemas
# ---------------------------------------------------------------------------


class OrderCreate(BaseModel):
    """
    Schema for creating a new customer order.

    Validates order type and requires at least one line item.
    Custom orders can include a free-form JSON spec bag via custom_specs.
    """

    customer_id: Optional[UUID] = None
    order_type: str = Field(default="standard", pattern="^(standard|custom)$")
    items: List[OrderItemCreate] = Field(min_length=1)

    # Custom order fields
    custom_specs: Optional[Dict[str, Any]] = None
    reference_image_url: Optional[str] = None

    # Financials
    estimated_amount: Decimal = Field(default=Decimal("0.00"), ge=0)
    advance_paid: Decimal = Field(default=Decimal("0.00"), ge=0)

    # Dates
    expected_delivery: Optional[date] = None
    notes: Optional[str] = None


class OrderStatusUpdate(BaseModel):
    """Schema for updating the lifecycle status of an order."""

    status: str = Field(
        pattern="^(draft|confirmed|in_production|ready|delivered|cancelled)$"
    )
    notes: Optional[str] = None


class OrderResponse(BaseModel):
    """
    Data Transfer Object for a complete order, including all line items.

    Includes computed balance_due for convenience on the client.
    """

    id: UUID
    order_number: str
    customer_id: Optional[UUID] = None
    order_type: str
    status: str

    custom_specs: Optional[Dict[str, Any]] = None
    reference_image_url: Optional[str] = None

    estimated_amount: Decimal
    advance_paid: Decimal
    final_amount: Decimal
    balance_due: Decimal = Decimal("0.00")

    expected_delivery: Optional[date] = None
    delivered_at: Optional[datetime] = None
    order_date: datetime
    created_by: UUID
    notes: Optional[str] = None

    items: List[OrderItemResponse] = []

    created_at: datetime
    updated_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)

    @model_validator(mode="after")
    def compute_balance_due(self) -> "OrderResponse":
        """Compute remaining balance = final_amount (or estimated) - advance_paid."""
        base = self.final_amount if self.final_amount > 0 else self.estimated_amount
        self.balance_due = max(Decimal("0.00"), base - self.advance_paid)
        return self
