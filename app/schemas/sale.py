from datetime import date, datetime
from decimal import Decimal
from typing import Dict, List, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class SaleItemCreate(BaseModel):
    """
    Schema for adding a product to a new sale transaction.
    """

    item_id: UUID
    quantity: int

    @field_validator("quantity")
    @classmethod
    def validate_quantity(cls, v):
        if v <= 0:
            raise ValueError("quantity must be greater than zero")
        return v


PayMethod = Literal["cash", "upi", "card", "bank", "cheque"]


class SalePaymentIn(BaseModel):
    """One payment taken on a bill (at the till, or added later)."""

    amount: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    method: PayMethod = "cash"
    reference: Optional[str] = Field(default=None, max_length=80)
    note: Optional[str] = Field(default=None, max_length=200)


class SalePaymentCreate(SalePaymentIn):
    paid_on: Optional[date] = None  # default: today


class SalePaymentVoid(BaseModel):
    """Why a payment is taken off the bill (it stays on record)."""

    reason: str = Field(min_length=1, max_length=300)

    @field_validator("reason")
    @classmethod
    def not_blank(cls, v):
        if not v.strip():
            raise ValueError("reason is required")
        return v.strip()


class SalePaymentResponse(BaseModel):
    id: UUID
    amount: Decimal
    method: str
    reference: Optional[str] = None
    note: Optional[str] = None
    paid_on: date
    created_at: datetime
    voided_at: Optional[datetime] = None
    void_reason: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class SaleCreate(BaseModel):
    """
    Schema for creating a new sales transaction.
    """

    customer_id: Optional[UUID] = None
    user_id: Optional[UUID] = None
    client_id: Optional[UUID] = None  # For idempotency
    items: List[SaleItemCreate]
    order_type: str = "in-store"
    # Where it was sold. Left out, the Shop is used when there is exactly one.
    location_id: Optional[UUID] = None
    discount: Decimal = Field(default=Decimal("0.00"), ge=0)
    payment_method: Optional[str] = None
    payment_status: str = "pending"
    order_status: str = "completed"
    # Given: these are the payments (status and method follow them). Left out: the old
    # way, payment_status "paid" + payment_method = one payment of the whole bill.
    payments: Optional[List[SalePaymentIn]] = None


class SaleItemResponse(BaseModel):
    """
    Data Transfer Object for item details within a sale.
    """

    id: UUID
    item_id: Optional[UUID] = None  # none for a custom piece or other charges
    item_name: Optional[str] = None
    quantity: int
    price_at_sale: Decimal
    gst_percent: Decimal = Decimal("0.00")
    cgst_amount: Decimal = Decimal("0.00")
    sgst_amount: Decimal = Decimal("0.00")
    total_price: Decimal = Decimal("0.00")
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class SaleResponse(BaseModel):
    """
    Data Transfer Object for a complete sales transaction.
    """

    id: UUID
    invoice_number: Optional[str] = None
    invoice_date: Optional[datetime] = None
    customer_id: Optional[UUID] = None
    user_id: Optional[UUID] = None
    location_id: Optional[UUID] = None
    order_type: str
    sub_total: Decimal
    tax_total: Decimal
    discount: Decimal
    total_amount: Decimal
    payment_status: str
    payment_method: Optional[str] = None
    order_status: str
    created_at: datetime
    updated_at: Optional[datetime] = None
    items: List[SaleItemResponse] = []
    amount_paid: Decimal = Decimal("0.00")
    balance_due: Decimal = Decimal("0.00")
    payments: List[SalePaymentResponse] = []
    returned_total: Decimal = Decimal("0.00")
    returned_quantities: Dict[UUID, int] = {}

    model_config = ConfigDict(from_attributes=True)


class SaleUpdate(BaseModel):
    """Corrections to a bill's customer and payment details. Only the fields sent change."""

    customer_id: Optional[UUID] = None
    payment_status: Optional[Literal["pending", "partial", "paid"]] = None
    payment_method: Optional[str] = Field(default=None, max_length=30)


class SaleCancel(BaseModel):
    """Why a bill is being cancelled (it stays in the bill history)."""

    reason: str = Field(min_length=3, max_length=300)


class ReturnLineIn(BaseModel):
    sale_item_id: UUID
    quantity: int = Field(gt=0)


class ReturnCreate(BaseModel):
    """Goods handed back from a bill. restock=False for damaged goods."""

    items: List[ReturnLineIn] = Field(min_length=1)
    reason: str = Field(min_length=1, max_length=300)
    refund_method: Literal["cash", "upi", "card", "bank", "cheque", "store_credit"]
    restock: bool = True

    @field_validator("reason")
    @classmethod
    def not_blank(cls, v):
        if not v.strip():
            raise ValueError("reason is required")
        return v.strip()


class ReturnItemResponse(BaseModel):
    sale_item_id: UUID
    item_name: Optional[str] = None
    quantity: int
    taxable_value: Decimal
    cgst_amount: Decimal
    sgst_amount: Decimal
    amount: Decimal

    model_config = ConfigDict(from_attributes=True)


class ReturnResponse(BaseModel):
    id: UUID
    credit_note_number: str
    sale_id: UUID
    invoice_number: Optional[str] = None
    reason: str
    refund_method: str
    refund_amount: Decimal
    restocked: bool
    created_at: datetime
    items: List[ReturnItemResponse] = []

    model_config = ConfigDict(from_attributes=True)
