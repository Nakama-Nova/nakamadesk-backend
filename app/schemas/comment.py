"""Schemas for staff moderation of customer comments."""

from datetime import datetime
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class CommentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    order_number: str
    display_name: str
    place: Optional[str] = None
    piece: Optional[str] = None
    body: str
    status: str
    created_at: datetime
    decided_at: Optional[datetime] = None


class CommentDecision(BaseModel):
    status: Literal["approved", "hidden"]
