import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, Numeric, String, text
from sqlalchemy.dialects.postgresql import UUID

from app.db.base import Base
from app.models.enums import UserRole


class User(Base):
    """
    SQLAlchemy model for system users and workforce.

    Manages authentication credentials, Role-Based Access Control (RBAC) levels,
    and profile data for both administrative staff and field workers.
    """

    __tablename__ = "users"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    username = Column(String, unique=True, index=True, nullable=False)
    email = Column(String, unique=True, index=True, nullable=False)
    password_hash = Column(String, nullable=False)
    full_name = Column(String, nullable=True)
    phone = Column(String, nullable=True)
    role = Column(
        String, nullable=False, default=UserRole.WORKER
    )  # owner, manager, sales, achari, worker
    status = Column(String, default="active")  # active, inactive, suspended
    is_active = Column(Boolean, default=True)
    base_daily_wage = Column(Numeric(10, 2), default=0.0)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
