import uuid
from datetime import date, datetime, timezone

from sqlalchemy import Boolean, Column, Date, DateTime, ForeignKey, String, text
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
