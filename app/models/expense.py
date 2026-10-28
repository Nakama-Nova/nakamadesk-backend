import uuid
from datetime import datetime, timezone

from sqlalchemy import (
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


class Expense(Base):
    """
    Money the business spent that is not raw-material buying or wages: rent,
    electricity, transport, tools, repairs and so on. Never deleted: a wrong entry
    is voided (with a reason) so the books keep their history.
    """

    __tablename__ = "expenses"
    __table_args__ = (Index("ix_expenses_spent_on", "spent_on"),)

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    spent_on = Column(Date, nullable=False)
    category = Column(String, nullable=False)
    amount = Column(Numeric(12, 2), nullable=False)
    payee = Column(String, nullable=True)
    method = Column(String, nullable=False, default="cash", server_default="cash")
    notes = Column(Text, nullable=True)
    # paint, wood, polish... spent on one restoration or finishing project
    restoration_id = Column(
        UUID(as_uuid=True),
        ForeignKey("restoration_projects.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    created_by = Column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=text("now()"),
    )
    voided_at = Column(DateTime(timezone=True), nullable=True)
    voided_by = Column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    void_reason = Column(String, nullable=True)
