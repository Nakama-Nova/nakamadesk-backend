"""The website's top-bar announcement and the ribbons on product photos."""

from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.item import Item
from app.models.site_content import ProductRibbon, SiteAnnouncement
from app.repositories.base import AbstractUnitOfWork
from app.schemas.public import PublicAnnouncement
from app.schemas.site import AnnouncementIn, AnnouncementOut, RibbonRow

IST = timezone(timedelta(hours=5, minutes=30))


def today_india() -> date:
    return datetime.now(timezone.utc).astimezone(IST).date()


def _current(db: Session) -> Optional[SiteAnnouncement]:
    return (
        db.query(SiteAnnouncement).order_by(SiteAnnouncement.updated_at.desc()).first()
    )


def public_announcement(db: Session) -> Optional[PublicAnnouncement]:
    """The line to show now, or None (switched off, not started, or past its end date)."""
    a = _current(db)
    if not a or not a.is_live(today_india()):
        return None
    return PublicAnnouncement(
        message=a.message,
        link_url=a.link_url,
        link_label=(a.link_label or "Learn more") if a.link_url else None,
    )


def _out(a: SiteAnnouncement) -> AnnouncementOut:
    return AnnouncementOut(
        message=a.message,
        link_url=a.link_url,
        link_label=a.link_label,
        starts_on=a.starts_on,
        ends_on=a.ends_on,
        enabled=bool(a.enabled),
        showing_now=a.is_live(today_india()),
        updated_at=a.updated_at,
    )


def staff_announcement(db: Session) -> Optional[AnnouncementOut]:
    a = _current(db)
    return _out(a) if a else None


def save_announcement(
    db: Session, data: AnnouncementIn, user_id: Optional[UUID]
) -> AnnouncementOut:
    """One announcement is kept and edited, not a growing list."""
    a = _current(db) or SiteAnnouncement()
    a.message = data.message
    a.link_url = data.link_url
    a.link_label = data.link_label
    a.starts_on = data.starts_on
    a.ends_on = data.ends_on
    a.enabled = data.enabled
    a.updated_by = user_id
    a.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
    db.add(a)
    db.commit()
    db.refresh(a)
    return _out(a)


def ribbons_for(db: Session, item_ids: List[UUID]) -> Dict[UUID, str]:
    if not item_ids:
        return {}
    rows = db.query(ProductRibbon).filter(ProductRibbon.item_id.in_(item_ids)).all()
    return {r.item_id: r.ribbon for r in rows}


def list_ribbon_rows(uow: AbstractUnitOfWork) -> List[RibbonRow]:
    items = (
        uow.session.query(Item)
        .filter(Item.is_published.is_(True), Item.is_active.is_(True))
        .order_by(Item.name)
        .all()
    )
    have = ribbons_for(uow.session, [i.id for i in items])
    return [
        RibbonRow(item_id=i.id, name=i.name, slug=i.slug, ribbon=have.get(i.id))
        for i in items
    ]


def set_ribbon(
    uow: AbstractUnitOfWork, item_id: UUID, ribbon: Optional[str]
) -> RibbonRow:
    item = uow.session.query(Item).filter(Item.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    with uow:
        row = (
            uow.session.query(ProductRibbon)
            .filter(ProductRibbon.item_id == item_id)
            .first()
        )
        if ribbon is None:
            if row:
                uow.session.delete(row)
        elif row:
            row.ribbon = ribbon
            row.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
        else:
            uow.session.add(ProductRibbon(item_id=item_id, ribbon=ribbon))
        uow.commit()
    return RibbonRow(item_id=item.id, name=item.name, slug=item.slug, ribbon=ribbon)
