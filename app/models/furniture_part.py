import uuid

from sqlalchemy import Column, ForeignKey, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import UUID

from app.db.base import Base


class FurniturePart(Base):
    """
    One placed piece of a furniture item (a leg, a rail, the top...). To model
    four identical legs, add four rows: the Lab groups identical pieces in the
    cutting list.

    Sizes and positions are formulas of the overall size (L, W, H in mm), see
    app/services/formula.py. Axes: L is left-right (X), W is front-back (Z),
    H is up (Y). `axis` says which way the piece's LENGTH runs; its width and
    thickness fill the other two. x/y/z give the piece's lowest corner.
    """

    __tablename__ = "furniture_parts"

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
    name = Column(String, nullable=False)
    wood_type = Column(String, nullable=True)
    raw_material_id = Column(
        UUID(as_uuid=True),
        ForeignKey("raw_materials.id", ondelete="SET NULL"),
        nullable=True,
    )
    l_expr = Column(String, nullable=False)  # length
    w_expr = Column(String, nullable=False)  # width
    t_expr = Column(String, nullable=False)  # thickness
    axis = Column(String, nullable=False, default="x", server_default="x")  # x | y | z
    x_expr = Column(String, nullable=False, default="0", server_default="0")
    y_expr = Column(String, nullable=False, default="0", server_default="0")
    z_expr = Column(String, nullable=False, default="0", server_default="0")
    assembly_step = Column(Integer, nullable=False, default=1, server_default="1")
    joinery_note = Column(Text, nullable=True)
    model_asset_id = Column(
        UUID(as_uuid=True),
        ForeignKey("product_assets.id", ondelete="SET NULL"),
        nullable=True,
    )
    sort = Column(Integer, nullable=False, default=0, server_default="0")
