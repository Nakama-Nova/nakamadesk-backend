from pydantic import BaseModel, field_validator
from typing import List, Union, Dict, Any, Optional
from datetime import datetime, date
from uuid import UUID
from decimal import Decimal
from enum import Enum


class SyncAction(str, Enum):
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"


class SaleItemPayload(BaseModel):
    """
    Schema for sale item data in a synchronization payload.
    """

    product_id: UUID
    quantity: int
    price: Decimal


class SalePayload(BaseModel):
    """
    Schema for sales transaction data in a synchronization payload.
    """

    id: Optional[UUID] = None
    customer_id: UUID
    items: List[SaleItemPayload]
    total_amount: Decimal
    payment_method: str
    invoice_number: Optional[str] = None


class ItemPayload(BaseModel):
    """
    Schema for inventory item data in a synchronization payload.
    """

    id: Optional[UUID] = None
    name: str
    sku: str
    selling_price: Decimal
    current_stock: int


class AttendancePayload(BaseModel):
    """
    Schema for workforce attendance data in a synchronization payload.
    """

    id: Optional[UUID] = None
    user_id: UUID
    date: date
    status: str
    check_in: Optional[datetime] = None
    check_out: Optional[datetime] = None
    total_hours: Optional[Decimal] = None
    daily_wage: Decimal


class SyncOperation(BaseModel):
    """
    Schema representing a single database operation for synchronization.
    """

    id: str  # Client's local operation ID / client_id
    entity: str  # "sale", "item", "attendance", "raw_material"
    action: SyncAction
    payload: Union[SalePayload, ItemPayload, AttendancePayload, Dict[str, Any]]
    updated_at: datetime

    @field_validator("payload", mode="before")
    @classmethod
    def validate_payload(cls, v, values):
        if isinstance(v, (SalePayload, ItemPayload, AttendancePayload)):
            return v

        entity = values.data.get("entity")
        if entity == "sale":
            return SalePayload(**v)
        elif entity == "item":
            return ItemPayload(**v)
        elif entity == "attendance":
            return AttendancePayload(**v)
        return v


class SyncPushRequest(BaseModel):
    """
    Request schema for a batch of sync operations from a client.
    """

    operations: List[SyncOperation]


class SyncOperationResult(BaseModel):
    """
    Schema for describing the outcome of a single sync operation.
    """

    client_id: str
    record_id: Optional[UUID] = None
    status: str
    error: Optional[str] = None


class SyncPushResponse(BaseModel):
    """
    Response schema summarizing the results of a batch sync push.
    """

    success: List[SyncOperationResult]
    failed: List[SyncOperationResult]


class SyncPullResponse(BaseModel):
    """
    Response schema providing new/updated data for client synchronization.
    """

    items: List[Dict[str, Any]]
    sales: List[Dict[str, Any]]
    attendance: List[Dict[str, Any]]
    raw_materials: List[Dict[str, Any]]
