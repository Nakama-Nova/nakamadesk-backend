import uuid

from sqlalchemy import Boolean, Column, ForeignKey, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import UUID

from app.db.base import Base


class FurnitureCarving(Base):
    """
    A design carved on one face of a furniture part. The picture and the design
    live ONCE in `designs`; each row here is only a placement. The same design on
    both side panels is one design and two rows.

    `face` is a face of the part's box in the assembled piece: left (x=0 side),
    right, front (z=0 side), back, top, bottom. The picture is centred on the
    face, scaled to fit inside `margin_mm` of the edge, keeping its proportions.
    """

    __tablename__ = "furniture_carvings"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    item_id = Column(
        UUID(as_uuid=True),
        ForeignKey("items.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    part_id = Column(
        UUID(as_uuid=True),
        ForeignKey("furniture_parts.id", ondelete="SET NULL"),
        nullable=True,
    )
    design_id = Column(
        UUID(as_uuid=True),
        ForeignKey("designs.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    face = Column(String, nullable=False)
    margin_mm = Column(Integer, nullable=False, default=0, server_default="0")
    mirror = Column(Boolean, nullable=False, default=False, server_default="false")
    note = Column(Text, nullable=True)
    sort = Column(Integer, nullable=False, default=0, server_default="0")
