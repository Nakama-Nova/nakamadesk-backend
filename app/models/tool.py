import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import UUID

from app.db.base import Base


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Tool(Base):
    """
    A durable hand tool or small power tool (chisel set, clamp, drill) that is handed
    to a person and handed back. Consumables such as adhesive are stock, not tools.
    Tools are retired, never deleted.
    """

    __tablename__ = "tools"
    __table_args__ = (
        CheckConstraint(
            "status IN ('available', 'issued', 'lost', 'broken', 'retired')",
            name="ck_tools_status",
        ),
        CheckConstraint(
            "condition IN ('new', 'good', 'worn', 'damaged')", name="ck_tools_condition"
        ),
    )

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    tag = Column(String, unique=True, nullable=False)
    name = Column(String, nullable=False)
    category = Column(String, nullable=True)  # carving, measuring, power...
    location_id = Column(
        UUID(as_uuid=True), ForeignKey("plant_units.id"), nullable=True, index=True
    )
    purchase_date = Column(Date, nullable=True)
    purchase_cost = Column(Numeric(12, 2), nullable=True)
    status = Column(
        String, nullable=False, default="available", server_default="available"
    )
    condition = Column(String, nullable=False, default="good", server_default="good")
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=_now)
    updated_at = Column(DateTime, nullable=False, default=_now, onupdate=_now)


class ToolIssue(Base):
    """A tool handed to a person. Open while returned_at is empty. Never edited."""

    __tablename__ = "tool_issues"
    __table_args__ = (
        Index("ix_tool_issues_tool_open", "tool_id", "returned_at"),
        CheckConstraint(
            "outcome IS NULL OR outcome IN ('ok', 'damaged', 'lost')",
            name="ck_tool_issues_outcome",
        ),
    )

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    tool_id = Column(UUID(as_uuid=True), ForeignKey("tools.id"), nullable=False)
    user_id = Column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True
    )  # who has it
    issued_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    issued_at = Column(DateTime, nullable=False, default=_now)
    condition_out = Column(String, nullable=False)
    returned_at = Column(DateTime, nullable=True)
    outcome = Column(String, nullable=True)  # ok, damaged, lost
    condition_in = Column(String, nullable=True)
    notes = Column(Text, nullable=True)
