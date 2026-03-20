from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from datetime import datetime

from app.db.deps import get_db, get_current_user
from app.models.user import User
from app.schemas.sync import SyncPushRequest, SyncPushResponse, SyncPullResponse
from app.services.sync_service import process_push_sync, pull_sync

router = APIRouter(prefix="/sync", tags=["Offline Sync"])


@router.post("/push", response_model=SyncPushResponse)
def push_sync_operations(
    request: SyncPushRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Client uploads its outbox. Backend processes sequentially in nested transactions.
    """
    return process_push_sync(db, request.operations)


@router.get("/pull", response_model=SyncPullResponse)
def pull_sync_updates(
    last_sync: datetime,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Client pulls incremental updates mapped since `last_sync`.
    """
    return pull_sync(db, last_sync)
