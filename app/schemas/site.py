"""Schemas for staff editing of the website's announcement bar and product ribbons."""

import re
from datetime import date, datetime
from typing import List, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

Ribbon = Literal["new", "bestseller", "few_left", "offer"]


class AnnouncementIn(BaseModel):
    message: str = Field(min_length=3, max_length=160)
    link_url: Optional[str] = Field(default=None, max_length=300)
    link_label: Optional[str] = Field(default=None, max_length=30)
    starts_on: Optional[date] = None
    ends_on: Optional[date] = None
    enabled: bool = True

    @field_validator("message", "link_label")
    @classmethod
    def _tidy(cls, v):
        if v is None:
            return v
        v = " ".join(v.split())
        return v or None

    @field_validator("link_url")
    @classmethod
    def _safe_link(cls, v):
        """Only a page on this site (/shop) or an https address: never javascript: and the like."""
        if v is None or not v.strip():
            return None
        v = v.strip()
        if re.fullmatch(r"/[A-Za-z0-9\-._~/?#=&%]*", v) and not v.startswith("//"):
            return v
        if re.fullmatch(r"https://[^\s<>\"']+", v):
            return v
        raise ValueError(
            "The link must be a page on this site (like /shop) or start with https://"
        )

    @model_validator(mode="after")
    def _dates(self):
        if self.starts_on and self.ends_on and self.ends_on < self.starts_on:
            raise ValueError("The end date must not be before the start date")
        return self


class AnnouncementOut(BaseModel):
    message: str
    link_url: Optional[str] = None
    link_label: Optional[str] = None
    starts_on: Optional[date] = None
    ends_on: Optional[date] = None
    enabled: bool
    showing_now: bool
    updated_at: datetime


class RibbonIn(BaseModel):
    ribbon: Optional[Ribbon] = None  # null removes the ribbon


class RibbonRow(BaseModel):
    item_id: UUID
    name: str
    slug: Optional[str] = None
    ribbon: Optional[str] = None


# ---- the home-page banner
YOUTUBE = re.compile(
    r"^https?://(?:www\.|m\.)?(?:youtube\.com/(?:watch\?(?:[^#]*&)?v=|shorts/|embed/|live/)|youtu\.be/)"
    r"([A-Za-z0-9_-]{11})(?:[?&#/].*)?$"
)
INSTAGRAM = re.compile(
    r"^https?://(?:www\.)?instagram\.com/(?:[A-Za-z0-9_.]+/)?(reels?|p|tv)/([A-Za-z0-9_-]{5,30})(?:[/?#].*)?$"
)


def read_video_link(kind: str, link: str):
    """(video id, variant) from a pasted YouTube or Instagram link, or None if it is not one."""
    link = (link or "").strip()
    if kind == "youtube":
        m = YOUTUBE.match(link)
        return (m.group(1), None) if m else None
    if kind == "instagram":
        m = INSTAGRAM.match(link)
        if not m:
            return None
        return (m.group(2), "reel" if m.group(1) in ("reel", "reels", "tv") else "p")
    return None


class BannerIn(BaseModel):
    headline: str = Field(min_length=3, max_length=80)
    body: Optional[str] = Field(default=None, max_length=240)
    media_kind: Literal["none", "image", "youtube", "instagram"] = "none"
    media_link: Optional[str] = Field(
        default=None, max_length=300
    )  # the YouTube / Instagram link
    button_url: Optional[str] = Field(default=None, max_length=300)
    button_label: Optional[str] = Field(default=None, max_length=30)
    starts_on: Optional[date] = None
    ends_on: Optional[date] = None
    enabled: bool = True

    @field_validator("headline", "body", "button_label")
    @classmethod
    def _tidy(cls, v):
        if v is None:
            return v
        return " ".join(v.split()) or None

    @field_validator("button_url")
    @classmethod
    def _safe_link(cls, v):
        return AnnouncementIn._safe_link.__func__(
            cls, v
        )  # the same rule as the announcement

    @model_validator(mode="after")
    def _check(self):
        if self.starts_on and self.ends_on and self.ends_on < self.starts_on:
            raise ValueError("The end date must not be before the start date")
        if self.media_kind in ("youtube", "instagram"):
            if not read_video_link(self.media_kind, self.media_link or ""):
                what = (
                    "YouTube"
                    if self.media_kind == "youtube"
                    else "Instagram reel or post"
                )
                raise ValueError(f"That does not look like a {what} link")
        return self


class BannerOut(BaseModel):
    headline: str
    body: Optional[str] = None
    media_kind: str
    media_link: Optional[str] = (
        None  # a link that opens the video, to show staff what is saved
    )
    image_url: Optional[str] = None
    button_url: Optional[str] = None
    button_label: Optional[str] = None
    starts_on: Optional[date] = None
    ends_on: Optional[date] = None
    enabled: bool
    showing_now: bool
    updated_at: datetime


# ---- visitor numbers
class StatDay(BaseModel):
    day: date
    views: int
    events: int


class StatRow(BaseModel):
    key: str
    count: int


class StatsOut(BaseModel):
    days: int
    views: int
    pages: List[StatRow]  # most viewed first
    events: List[StatRow]  # action name and how many times
    per_day: List[StatDay]
