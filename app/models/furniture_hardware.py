import uuid

from sqlalchemy import Column, ForeignKey, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import UUID

from app.db.base import Base


class FurnitureHardware(Base):
    """
    Something that is not cut from the wood list: screws, nails, dowels, bolts,
    brackets, glue, and clamps. One row is one line of the hardware list.

    `qty_expr` is a formula of the overall size (L, W, H in mm), like a part's
    size, and the Lab rounds it UP to a whole number. Clamps (kind 'clamp') are
    tools, not stock: the Lab lists them per build step, never in the stock list.
    `part_id` (optional) says which part the line belongs to; `assembly_step`
    (optional) which build step uses it. With only part_id, the part's step is used.
    """

    __tablename__ = "furniture_hardware"

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
    kind = Column(String, nullable=False)
    name = Column(String, nullable=False)
    qty_expr = Column(String, nullable=False)
    part_id = Column(
        UUID(as_uuid=True),
        ForeignKey("furniture_parts.id", ondelete="SET NULL"),
        nullable=True,
    )
    assembly_step = Column(Integer, nullable=True)
    note = Column(Text, nullable=True)
    sort = Column(Integer, nullable=False, default=0, server_default="0")
