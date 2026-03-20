from sqlalchemy import Column, String, Boolean, DateTime, text, Numeric
from sqlalchemy.dialects.postgresql import UUID
from datetime import datetime, timezone
import uuid

from app.db.base import Base


class User(Base):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("gen_random_uuid()"))
    username = Column(String, unique=True, index=True, nullable=False)
    email = Column(String, unique=True, index=True, nullable=False)
    password_hash = Column(String, nullable=False)
    full_name = Column(String, nullable=True)
    phone = Column(String, nullable=True)
    role = Column(String, default="worker")  # owner, manager, sales, achari, worker, customer
    status = Column(String, default="active") # active, inactive, suspended
    is_active = Column(Boolean, default=True)
    base_daily_wage = Column(Numeric(10, 2), default=0.0)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
