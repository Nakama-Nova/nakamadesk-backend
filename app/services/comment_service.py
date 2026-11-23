"""
Customer comments: a customer who ordered can write one comment per order; staff approve it
before it shows on the website.
"""

import logging
from datetime import datetime, timezone
from typing import List, Optional
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

from app.models.customer_comment import CustomerComment
from app.models.order import Order
from app.repositories.base import AbstractUnitOfWork
from app.schemas.comment import CommentOut
from app.schemas.public import CommentSubmit, PublicComment
from app.services import public_service

logger = logging.getLogger(__name__)

# A comment can be written once the shop has confirmed the order (not a draft, not cancelled)
CAN_COMMENT = ("confirmed", "in_production", "ready", "delivered")
PUBLIC_LIMIT = 12


def _clean(text: Optional[str]) -> str:
    """One line per paragraph, no stray spaces, no control characters."""
    kept = "".join(ch for ch in (text or "") if ch in "\n" or ch.isprintable())
    return "\n".join(line.strip() for line in kept.splitlines()).strip()


def submit(uow: AbstractUnitOfWork, req: CommentSubmit) -> None:
    """Store a comment as pending. Raises 404 for a wrong order/phone, 409 when it cannot be taken."""
    if (
        req.website.strip()
    ):  # the hidden field: only a bot fills it. Say "received" and keep nothing.
        return
    if not req.consent:
        raise HTTPException(
            status_code=422,
            detail="Please tick the box to let us show your comment on the website",
        )
    order = public_service.order_for_phone(uow, req.order_number, req.phone)
    if order.status not in CAN_COMMENT:
        raise HTTPException(
            status_code=409,
            detail="You can leave a comment once we have confirmed your order",
        )
    name, body = _clean(req.display_name), _clean(req.text)
    if len(name) < 2 or len(body) < 10:
        raise HTTPException(status_code=422, detail="Please write a little more")
    exists = (
        uow.session.query(CustomerComment.id)
        .filter(CustomerComment.order_id == order.id)
        .first()
    )
    if exists:
        raise HTTPException(
            status_code=409,
            detail="We already have your comment for this order. Thank you!",
        )
    piece = order.items[0].item_name if order.items else None
    comment = CustomerComment(
        order_id=order.id,
        display_name=name[:60],
        place=(_clean(req.place)[:60] or None) if req.place else None,
        piece=(piece or None) and piece[:150],
        body=body,
        status="pending",
    )
    try:
        with uow:
            uow.session.add(comment)
            uow.commit()
    except IntegrityError:  # two sends at once: the second loses, same answer as above
        raise HTTPException(
            status_code=409,
            detail="We already have your comment for this order. Thank you!",
        ) from None
    logger.info("customer comment received for order %s", order.order_number)


def list_public(
    uow: AbstractUnitOfWork, limit: int = PUBLIC_LIMIT
) -> List[PublicComment]:
    """Approved comments, newest approval first. Nothing else about the customer or order."""
    rows = (
        uow.session.query(CustomerComment)
        .filter(CustomerComment.status == "approved")
        .order_by(CustomerComment.decided_at.desc(), CustomerComment.created_at.desc())
        .limit(limit)
        .all()
    )
    return [
        PublicComment(name=r.display_name, place=r.place, piece=r.piece, text=r.body)
        for r in rows
    ]


def list_for_staff(uow: AbstractUnitOfWork, status: Optional[str]) -> List[CommentOut]:
    q = uow.session.query(CustomerComment, Order.order_number).join(
        Order, Order.id == CustomerComment.order_id
    )
    if status and status != "all":
        q = q.filter(CustomerComment.status == status)
    rows = q.order_by(CustomerComment.created_at.desc()).limit(200).all()
    return [
        CommentOut(
            id=c.id,
            order_number=number,
            display_name=c.display_name,
            place=c.place,
            piece=c.piece,
            body=c.body,
            status=c.status,
            created_at=c.created_at,
            decided_at=c.decided_at,
        )
        for c, number in rows
    ]


def decide(
    uow: AbstractUnitOfWork, comment_id: UUID, status: str, user_id: Optional[UUID]
) -> CommentOut:
    with uow:
        c = (
            uow.session.query(CustomerComment)
            .filter(CustomerComment.id == comment_id)
            .first()
        )
        if not c:
            raise HTTPException(status_code=404, detail="Comment not found")
        c.status = status
        c.decided_at = datetime.now(timezone.utc).replace(tzinfo=None)
        c.decided_by = user_id
        uow.commit()
    number = (
        uow.session.query(Order.order_number).filter(Order.id == c.order_id).scalar()
        or ""
    )
    return CommentOut(
        id=c.id,
        order_number=number,
        display_name=c.display_name,
        place=c.place,
        piece=c.piece,
        body=c.body,
        status=c.status,
        created_at=c.created_at,
        decided_at=c.decided_at,
    )
