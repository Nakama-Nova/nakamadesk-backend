from datetime import datetime, timezone
from sqlalchemy import Column, DateTime, String, text
from sqlalchemy.dialects.postgresql import UUID
import uuid

from app.db.base import Base


class Supplier(Base):
    __tablename__ = "suppliers"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    name = Column(String, index=True, nullable=False)
    contact_person = Column(String)
    phone = Column(String, index=True)
    email = Column(String)
    address = Column(String)
    gstin = Column(String)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
