from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Dict, List, Optional, Union
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

# Upper bound on operations per /sync/push request. Keeps a single request's
# DB transaction bounded even for a legitimate-but-corrupted/runaway client
# outbox; matches /sync/pull's existing limit=500 cap.
MAX_SYNC_PUSH_OPERATIONS = 500


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

    Fields beyond `id` are optional so that partial/delta updates (e.g. a
    stock-only change) don't have to resend the full record. `name` and
    `sku` are still required to actually create a new item — that's
    enforced in ItemSyncHandler.apply_create, not here, so a missing field
    fails just that operation instead of the whole sync batch.
    """

    id: Optional[UUID] = None
    name: Optional[str] = None
    sku: Optional[str] = None
    selling_price: Optional[Decimal] = None
    current_stock: Optional[int] = None


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


class RawMaterialPayload(BaseModel):
    """
    Schema for raw material data in a synchronization payload.

    Mirrors ItemPayload: fields beyond `id` are optional so partial/delta
    updates (e.g. a stock-only change) don't have to resend the full
    record. `name` is still required to actually create a new raw
    material — enforced in RawMaterialSyncHandler.apply_create, not here.
    """

    id: Optional[UUID] = None
    name: Optional[str] = None
    unit: Optional[str] = None
    current_price: Optional[Decimal] = None
    stock: Optional[Decimal] = None


class SyncOperation(BaseModel):
    """
    Schema representing a single database operation for synchronization.
    """

    id: str  # Client's local operation ID / client_id
    entity: str  # "sale", "item", "attendance", "raw_material"
    action: SyncAction
    payload: Union[
        SalePayload, ItemPayload, AttendancePayload, RawMaterialPayload, Dict[str, Any]
    ]
    updated_at: datetime

    @field_validator("payload", mode="before")
    @classmethod
    def validate_payload(cls, v, values):
        if isinstance(
            v, (SalePayload, ItemPayload, AttendancePayload, RawMaterialPayload)
        ):
            return v

        entity = values.data.get("entity")
        if entity == "sale":
            return SalePayload(**v)
        elif entity == "item":
            return ItemPayload(**v)
        elif entity == "raw_material":
            return RawMaterialPayload(**v)
        elif entity == "attendance":
            return AttendancePayload(**v)
        return v


class SyncPushRequest(BaseModel):
    """
    Request schema for a batch of sync operations from a client.
    """

    operations: List[SyncOperation] = Field(..., max_length=MAX_SYNC_PUSH_OPERATIONS)


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
