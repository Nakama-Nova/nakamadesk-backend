from pydantic import BaseModel
from typing import List, Dict, Any, Optional
from datetime import datetime
from uuid import UUID

class SyncOperation(BaseModel):
    id: str # Client's local operation ID / client_id
    entity: str # "sale", "item", "attendance", "raw_material"
    action: str # "create", "update", "delete"
    payload: Dict[str, Any]
    updated_at: datetime # Timestamp from client
    
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
