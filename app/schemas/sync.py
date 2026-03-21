from pydantic import BaseModel, validator
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
    product_id: UUID
    quantity: int
    price: Decimal

class SalePayload(BaseModel):
    id: Optional[UUID] = None
    customer_id: UUID
    items: List[SaleItemPayload]
    total_amount: Decimal
    payment_method: str
    invoice_number: Optional[str] = None

class ItemPayload(BaseModel):
    id: Optional[UUID] = None
    name: str
    sku: str
    selling_price: Decimal
    current_stock: int

class AttendancePayload(BaseModel):
    id: Optional[UUID] = None
    user_id: UUID
    date: date
    status: str
    daily_wage: Decimal

class SyncOperation(BaseModel):
    id: str  # Client's local operation ID / client_id
    entity: str  # "sale", "item", "attendance", "raw_material"
    action: SyncAction
    payload: Union[SalePayload, ItemPayload, AttendancePayload, Dict[str, Any]]
    updated_at: datetime

    @validator("payload", pre=True)
    def validate_payload(cls, v, values):
        if isinstance(v, (SalePayload, ItemPayload, AttendancePayload)):
            return v
        
        entity = values.get("entity")
        if entity == "sale":
            return SalePayload(**v)
        elif entity == "item":
            return ItemPayload(**v)
        elif entity == "attendance":
            return AttendancePayload(**v)
        return v

class SyncPushRequest(BaseModel):
    operations: List[SyncOperation]

class SyncOperationResult(BaseModel):
    client_id: str
    record_id: Optional[UUID] = None
    status: str
    error: Optional[str] = None

class SyncPushResponse(BaseModel):
    success: List[SyncOperationResult]
    failed: List[SyncOperationResult]

class SyncPullResponse(BaseModel):
    items: List[Dict[str, Any]]
    sales: List[Dict[str, Any]]
    attendance: List[Dict[str, Any]]
    raw_materials: List[Dict[str, Any]]
