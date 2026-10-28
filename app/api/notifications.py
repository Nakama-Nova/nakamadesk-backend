"""
Things staff should be told about (new website orders, money received, stock running
low, finished 3D scans). The phone app and the desktop poll this every minute or so.

GET /notifications               -> {"server_time": ..., "items": []}: where to start from
GET /notifications?since=<time>  -> what happened after that time (UTC), oldest first

Keep `server_time` and send it as `since` next time; each item's `id` never repeats.
"""

from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.db.deps import get_current_user, get_db
from app.models.user import User
from app.services import notification_service

router = APIRouter(prefix="/notifications", tags=["Notifications"])


class Notice(BaseModel):
    id: str
    kind: str
    title: str
    body: str
    created_at: str
    screen: str
    target_id: Optional[str] = None


class NoticeFeed(BaseModel):
    server_time: str
    items: List[Notice]


@router.get("", response_model=NoticeFeed)
def get_notifications(
    since: Optional[datetime] = Query(
        default=None, description="UTC time to start after; leave out on the first call"
    ),
    limit: int = Query(default=50, ge=1, le=100),
    user: User = Depends(get_current_user),
    db=Depends(get_db),
):
    now = notification_service.now_utc()
    items = []
    if since is not None:
        items = notification_service.feed(db, user, since, limit)
    return {"server_time": now.isoformat(), "items": items}
