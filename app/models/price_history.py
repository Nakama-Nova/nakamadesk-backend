from datetime import datetime, timezone
from sqlalchemy import Column, DateTime, Numeric, ForeignKey, String, text
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.postgresql import UUID
import uuid

from app.db.base import Base


class RawMaterialPriceHistory(Base):
    __tablename__ = "raw_material_price_history"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    material_id = Column(
        UUID(as_uuid=True), ForeignKey("raw_materials.id"), index=True, nullable=False
    )
    price = Column(Numeric(10, 2), nullable=False)
    source = Column(String, default="MANUAL")  # MANUAL, PURCHASE, AI
    recorded_at = Column(
        DateTime, default=lambda: datetime.now(timezone.utc), index=True
    )

    material = relationship("RawMaterial")
