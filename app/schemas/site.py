"""Schemas for staff editing of the website's announcement bar and product ribbons."""

import re
from datetime import date, datetime
from typing import Literal, Optional
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
