from datetime import datetime, timezone
from sqlalchemy import Column, DateTime, String, ForeignKey, text
from sqlalchemy.dialects.postgresql import UUID
import uuid

from app.db.base import Base


class Customer(Base):
    __tablename__ = "customers"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("gen_random_uuid()"))
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    name = Column(String, nullable=False)
    phone = Column(String, index=True)
    email = Column(String, index=True)
    address = Column(String)
    pincode = Column(String)
    gstin = Column(String, nullable=True)
    customer_type = Column(String, default="retail") # retail, wholesale
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
