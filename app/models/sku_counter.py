from sqlalchemy import Column, Integer, String

from app.db.base import Base


class SkuCounter(Base):
    """
    One row per code prefix (FUR, SCU, ...) holding the last number handed out, so the
    codes come out in order (FUR-0001, FUR-0002) and never repeat. See
    sku_service.next_sku.
    """

    __tablename__ = "sku_counters"

    prefix = Column(String, primary_key=True)
    last_no = Column(Integer, nullable=False, default=0, server_default="0")
