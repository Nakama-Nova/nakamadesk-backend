from sqlalchemy import Column, String, text
from sqlalchemy.dialects.postgresql import UUID
import uuid

from app.db.base import Base


class Category(Base):
    """
    SQLAlchemy model for product categories.

    Defines broad groupings for inventory (e.g., 'Timber', 'Hardware')
    to aid in organization and reporting.
    """

    __tablename__ = "categories"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    name = Column(String, unique=True, index=True, nullable=False)
    description = Column(String, nullable=True)
