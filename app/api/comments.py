"""
Staff side of customer comments: see what was sent, approve it for the website, or hide it.

OWNER and MANAGER only. `/comments/review` is a small page for doing this in a browser:
it asks for the staff login, keeps the session in the page only (nothing is stored) and
lists comments waiting for a decision.
"""

from pathlib import Path
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse

from app.db.deps import get_uow
from app.iam.service import require
from app.models.user import User
from app.repositories.base import AbstractUnitOfWork
from app.schemas.comment import CommentDecision, CommentOut
from app.services import comment_service

router = APIRouter(prefix="/comments", tags=["Customer comments"])

PAGE = Path(__file__).resolve().parent.parent / "static" / "comments" / "review.html"
# The page has one inline script and talks only to this server
PAGE_HEADERS = {
    "Cache-Control": "no-store",
    "Content-Security-Policy": "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
}


@router.get("/review", include_in_schema=False)
def review_page():
    """The moderation page (it logs in with the normal staff login)."""
    return FileResponse(PAGE, media_type="text/html", headers=PAGE_HEADERS)


@router.get("", response_model=List[CommentOut])
def list_comments(
    status: Optional[str] = Query("pending", pattern="^(pending|approved|hidden|all)$"),
    current_user: User = Depends(require("comments:comment:review")),
    uow: AbstractUnitOfWork = Depends(get_uow),
):
    """Comments by status (default: waiting for a decision), newest first."""
    return comment_service.list_for_staff(uow, status)


@router.patch("/{comment_id}", response_model=CommentOut)
def decide_comment(
    comment_id: UUID,
    body: CommentDecision,
    current_user: User = Depends(require("comments:comment:review")),
    uow: AbstractUnitOfWork = Depends(get_uow),
):
    """Approve a comment (it appears on the website) or hide it."""
    return comment_service.decide(uow, comment_id, body.status, current_user.id)
