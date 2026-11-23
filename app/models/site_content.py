import uuid
from datetime import date, datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import UUID

from app.db.base import Base


def _now() -> datetime:
    """Naive UTC, like the other timestamp columns here."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class SiteAnnouncement(Base):
    """
    The line shown in the bar at the top of the website (an offer, a festival hours notice...).
    One row is kept and edited. It shows while `enabled` and today (India time) is between
    `starts_on` and `ends_on`, both optional and inclusive, so an offer switches itself off.
    """

    __tablename__ = "site_announcements"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    message = Column(String(160), nullable=False)
    link_url = Column(String(300), nullable=True)  # https://... or a path like /shop
    link_label = Column(String(30), nullable=True)
    starts_on = Column(Date, nullable=True)
    ends_on = Column(Date, nullable=True)
    enabled = Column(Boolean, nullable=False, default=True)
    updated_at = Column(DateTime, nullable=False, default=_now, onupdate=_now)
    updated_by = Column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    def is_live(self, today: date) -> bool:
        return bool(
            self.enabled
            and (self.starts_on is None or today >= self.starts_on)
            and (self.ends_on is None or today <= self.ends_on)
        )


class ProductRibbon(Base):
    """
    A small label staff put on a product's photo on the website: new, bestseller, few_left
    or offer. Kept apart from the item itself so the staff apps' item data does not change.
    No row means no ribbon.
    """

    __tablename__ = "product_ribbons"

    item_id = Column(
        UUID(as_uuid=True),
        ForeignKey("items.id", ondelete="CASCADE"),
        primary_key=True,
    )
    ribbon = Column(String(20), nullable=False)
    updated_at = Column(DateTime, nullable=False, default=_now, onupdate=_now)


class SiteBanner(Base):
    """
    The feature banner on the website's home page: a headline and a few words, with a picture
    or a video, and a button. One row is kept and edited. Shows while `enabled` and today
    (India time) is between `starts_on` and `ends_on` (both optional, inclusive).

    `media_kind`: none, image, youtube or instagram. For the two video kinds only the video's
    id is stored (read out of the link staff paste), and the address to embed is built from a
    fixed template, so no other address can ever be put in a page. `image_key` is a file in the
    public store, kept (and shrunk to web size) by the upload route.
    """

    __tablename__ = "site_banners"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    headline = Column(String(80), nullable=False)
    body = Column(String(240), nullable=True)
    media_kind = Column(String(12), nullable=False, default="none")
    video_id = Column(String(40), nullable=True)
    video_variant = Column(String(8), nullable=True)  # instagram: reel or p
    image_key = Column(String(300), nullable=True)
    image_storage = Column(String(12), nullable=True)
    button_url = Column(String(300), nullable=True)
    button_label = Column(String(30), nullable=True)
    starts_on = Column(Date, nullable=True)
    ends_on = Column(Date, nullable=True)
    enabled = Column(Boolean, nullable=False, default=False)
    updated_at = Column(DateTime, nullable=False, default=_now, onupdate=_now)
    updated_by = Column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    def is_live(self, today: date) -> bool:
        return bool(
            self.enabled
            and (self.starts_on is None or today >= self.starts_on)
            and (self.ends_on is None or today <= self.ends_on)
        )


class SiteHit(Base):
    """
    A daily count for the website's own visitor numbers: how many times a page was opened
    (`kind` view, `key` the page address) or an action happened (`kind` event, `key` its name).
    Only a count per day, page or action is kept. No address, device, cookie or any other
    identifier is stored, so visitors cannot be told apart or followed.
    """

    __tablename__ = "site_hits"

    day = Column(Date, primary_key=True)  # India date
    kind = Column(String(8), primary_key=True)  # view or event
    key = Column(String(120), primary_key=True)
    count = Column(Integer, nullable=False, default=0)
