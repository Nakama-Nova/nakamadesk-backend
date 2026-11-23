"""The website's top-bar announcement and the ribbons on product photos."""

import re
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models.item import Item
from app.models.site_content import (
    ProductRibbon,
    SiteAnnouncement,
    SiteBanner,
    SiteHit,
)
from app.repositories.base import AbstractUnitOfWork
from app.schemas.public import PublicAnnouncement, PublicBanner
from app.schemas.site import (
    AnnouncementIn,
    AnnouncementOut,
    BannerIn,
    BannerOut,
    RibbonRow,
    StatDay,
    StatRow,
    StatsOut,
    read_video_link,
)
from app.services import media_service
from app.services.object_store import S3Store

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


# --------------------------------------------------------------------------- home banner
# The banner's picture lives in the public store under a fixed "owner" id, like a product photo.
BANNER_MEDIA_ID = uuid.UUID("5174e000-0000-4000-8000-0000000000b1")
BANNER_IMAGE_MAX = 8 * 1024 * 1024


def _banner(db: Session) -> Optional[SiteBanner]:
    return db.query(SiteBanner).order_by(SiteBanner.updated_at.desc()).first()


def _embed(kind: str, video_id: Optional[str], variant: Optional[str]):
    """(embed address, plain watch address), built from fixed templates around the stored id."""
    if not video_id:
        return None, None
    if kind == "youtube":
        return (
            f"https://www.youtube-nocookie.com/embed/{video_id}?rel=0&playsinline=1",
            f"https://www.youtube.com/watch?v={video_id}",
        )
    if kind == "instagram":
        path = "reel" if variant == "reel" else "p"
        return (
            f"https://www.instagram.com/{path}/{video_id}/embed/",
            f"https://www.instagram.com/{path}/{video_id}/",
        )
    return None, None


def _image_url(b: SiteBanner) -> Optional[str]:
    return media_service.url(b.image_key) if b.image_key else None


def public_banner(db: Session) -> Optional[PublicBanner]:
    b = _banner(db)
    if not b or not b.is_live(today_india()):
        return None
    kind = b.media_kind
    if kind == "image" and not b.image_key:
        kind = "none"  # chose a picture but never uploaded one: show the words only
    embed, watch = _embed(kind, b.video_id, b.video_variant)
    return PublicBanner(
        headline=b.headline,
        body=b.body,
        media_kind=kind,
        image_url=_image_url(b) if kind == "image" else None,
        embed_url=embed,
        watch_url=watch,
        button_url=b.button_url,
        button_label=(b.button_label or "Learn more") if b.button_url else None,
    )


def _banner_out(b: SiteBanner) -> BannerOut:
    _, watch = _embed(b.media_kind, b.video_id, b.video_variant)
    return BannerOut(
        headline=b.headline,
        body=b.body,
        media_kind=b.media_kind,
        media_link=watch,
        image_url=_image_url(b),
        button_url=b.button_url,
        button_label=b.button_label,
        starts_on=b.starts_on,
        ends_on=b.ends_on,
        enabled=bool(b.enabled),
        showing_now=b.is_live(today_india()),
        updated_at=b.updated_at,
    )


def staff_banner(db: Session) -> Optional[BannerOut]:
    b = _banner(db)
    return _banner_out(b) if b else None


def save_banner(db: Session, data: BannerIn, user_id: Optional[UUID]) -> BannerOut:
    """One banner is kept and edited. Its picture (if any) is kept until staff upload another."""
    b = _banner(db) or SiteBanner()
    video = read_video_link(data.media_kind, data.media_link or "")
    b.headline = data.headline
    b.body = data.body
    b.media_kind = data.media_kind
    b.video_id, b.video_variant = video if video else (None, None)
    b.button_url = data.button_url
    b.button_label = data.button_label
    b.starts_on = data.starts_on
    b.ends_on = data.ends_on
    b.enabled = data.enabled
    b.updated_by = user_id
    b.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
    db.add(b)
    db.commit()
    db.refresh(b)
    return _banner_out(b)


def save_banner_image(
    db: Session,
    content_type: str,
    data: bytes,
    store: Optional[S3Store],
    user_id: Optional[UUID],
) -> BannerOut:
    """Keep an uploaded picture (shrunk to web size) for the banner, replacing the old one."""
    b = _banner(db)
    if not b:
        raise HTTPException(
            status_code=409, detail="Save the banner first, then add its picture"
        )
    kind, ext = media_service.validate(content_type, data)
    if kind != "image":
        raise HTTPException(status_code=415, detail="Only JPG, PNG or WebP pictures")
    if len(data) > BANNER_IMAGE_MAX:
        raise HTTPException(status_code=413, detail="Picture too large (max 8 MB)")
    ext, data, _ = media_service.optimise_image(ext, data)
    key, storage = media_service.save_public(BANNER_MEDIA_ID, ext, data, store)
    old_key, old_storage = b.image_key, b.image_storage
    try:
        b.image_key, b.image_storage = key, storage
        b.updated_by = user_id
        b.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
        db.commit()
    except Exception:
        db.rollback()
        media_service.delete(key, storage, store)
        raise
    if old_key:
        media_service.delete(old_key, old_storage or "local", store)
    db.refresh(b)
    return _banner_out(b)


# --------------------------------------------------------------------------- visitor numbers
# Only these actions are counted, and a page view must look like one of the site's own addresses,
# so the table cannot be filled with arbitrary text.
EVENTS = {
    "add_to_cart",
    "checkout_open",
    "order_placed",
    "enquiry_sent",
    "comment_sent",
    "video_play",
    "whatsapp_click",
}
PAGE = re.compile(r"^/[a-z0-9][a-z0-9\-/]{0,100}$|^/$")
BOTS = re.compile(r"bot|crawl|spider|slurp|preview|monitor|headless", re.I)


def record_hit(db: Session, kind: str, key: str, user_agent: str = "") -> None:
    """Add one to today's count for a page or action. Quietly ignores anything that is not a known page or action, and robots."""
    if BOTS.search(user_agent or ""):
        return
    key = key.strip().lower()
    if kind == "view":
        key = key.split("?")[0].split("#")[0].rstrip("/") or "/"
        if not PAGE.match(key):
            return
    elif kind == "event":
        if key not in EVENTS:
            return
    else:
        return
    stmt = pg_insert(SiteHit).values(day=today_india(), kind=kind, key=key, count=1)
    stmt = stmt.on_conflict_do_update(
        index_elements=[SiteHit.day, SiteHit.kind, SiteHit.key],
        set_={"count": SiteHit.count + 1},
    )
    db.execute(stmt)
    db.commit()


def _ranked(counts: Dict[str, int]) -> List[StatRow]:
    """Most first; ties in alphabetical order."""
    return [
        StatRow(key=k, count=v)
        for k, v in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    ]


def stats(db: Session, days: int) -> StatsOut:
    """Totals for the last `days` days (today included): views, top pages, actions, and a line per day."""
    since = today_india() - timedelta(days=days - 1)
    rows = db.query(SiteHit).filter(SiteHit.day >= since).all()
    pages: Dict[str, int] = {}
    events: Dict[str, int] = {}
    per: Dict[date, List[int]] = {}
    for r in rows:
        day = per.setdefault(r.day, [0, 0])
        if r.kind == "view":
            pages[r.key] = pages.get(r.key, 0) + r.count
            day[0] += r.count
        else:
            events[r.key] = events.get(r.key, 0) + r.count
            day[1] += r.count
    return StatsOut(
        days=days,
        views=sum(pages.values()),
        pages=_ranked(pages)[:15],
        events=_ranked(events),
        per_day=[
            StatDay(day=d, views=v[0], events=v[1]) for d, v in sorted(per.items())
        ],
    )
