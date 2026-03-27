from datetime import datetime
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.db.deps import get_db, get_current_user
from app.models.user import User
from app.schemas.sync import SyncPushRequest, SyncPushResponse, SyncPullResponse
from app.services.sync_service import process_push_sync, pull_sync
from app.repositories.sqlalchemy_repo import SQLAlchemyUnitOfWork

router = APIRouter(prefix="/sync", tags=["Offline Sync"])


@router.post("/push", response_model=SyncPushResponse)
def push_sync_operations(
    request: SyncPushRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Upload and process a batch of offline operations from the client outbox.

    Operations are processed sequentially in nested transactions to maintain consistency.
    Conflict resolution and idempotency are handled by the sync service.

    Args:
        request (SyncPushRequest): Payload containing a list of sync operations.
        current_user (User): Authenticated user uploading the data.
        db (Session): Database session.

    Returns:
        SyncPushResponse: Results of the processed operations, including any conflicts.
    """
    uow = SQLAlchemyUnitOfWork(db)
    return process_push_sync(uow, request.operations, current_user)


@router.get("/pull", response_model=SyncPullResponse)
def pull_sync_updates(
    last_sync: datetime,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Retrieve incremental updates from the server since the last successful sync.

    Args:
        last_sync (datetime): Timestamp of the client's last synchronization.
        current_user (User): Authenticated user requesting updates.
        db (Session): Database session.

    Returns:
        SyncPullResponse: List of updated records since `last_sync`.
    """
    uow = SQLAlchemyUnitOfWork(db)
    return pull_sync(uow, last_sync)
