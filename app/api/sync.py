from datetime import datetime

from fastapi import APIRouter, Depends, Query

from app.db.deps import get_current_user, get_uow
from app.models.user import User
from app.repositories.base import AbstractUnitOfWork
from app.schemas.sync import SyncPullResponse, SyncPushRequest, SyncPushResponse
from app.services.sync_service import process_push_sync, pull_sync

router = APIRouter(prefix="/sync", tags=["Offline Sync"])


@router.post("/push", response_model=SyncPushResponse)
def push_sync_operations(
    request: SyncPushRequest,
    current_user: User = Depends(get_current_user),
    uow: AbstractUnitOfWork = Depends(get_uow),
):
    """
    Upload and process a batch of offline operations from the client outbox.

    Operations are processed sequentially in nested transactions to maintain consistency.
    Conflict resolution and idempotency are handled by the sync service.

    Args:
        request (SyncPushRequest): Payload containing a list of sync operations.
        current_user (User): Authenticated user uploading the data.
        uow (AbstractUnitOfWork): Unit of Work for database operations.

    Returns:
        SyncPushResponse: Results of the processed operations, including any conflicts.
    """
    return process_push_sync(uow, request.operations, current_user)


@router.get("/pull", response_model=SyncPullResponse)
def pull_sync_updates(
    last_sync: datetime,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(get_current_user),
    uow: AbstractUnitOfWork = Depends(get_uow),
):
    """
    Retrieve incremental updates from the server since the last successful sync.

    Args:
        last_sync (datetime): Timestamp of the client's last synchronization.
        limit (int): Maximum records per entity.
        offset (int): Records to skip.
        current_user (User): Authenticated user requesting updates.
        uow (AbstractUnitOfWork): Unit of Work for database operations.

    Returns:
        SyncPullResponse: List of updated records since `last_sync`.
    """
    return pull_sync(uow, last_sync, limit, offset)
