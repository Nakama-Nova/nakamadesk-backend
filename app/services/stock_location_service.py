"""
Where stock is. Items and raw materials keep one TOTAL (items.current_stock,
raw_materials.stock), changed only through inventory_service. This module keeps the
per-location split of that total in stock_levels and moves stock between locations.

- A change made "at" a location adds the same amount to that location's level.
- A change with no location is "unassigned": total minus the sum of the levels.
- A transfer moves stock between locations (or in and out of unassigned) and leaves
  the total alone. It is the only operation that refuses to go below what is there;
  sales and adjustments at a location may go negative so the counter never stops.
"""

import uuid
from decimal import Decimal
from typing import Optional
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.inventory_movement import InventoryMovement
from app.models.item import Item
from app.models.plant_unit import PlantUnit
from app.models.raw_material import RawMaterial
from app.models.stock_level import StockLevel


def get_location(session: Session, location_id: UUID) -> PlantUnit:
    """An active location, or 400. Used for any location a client names."""
    unit = session.get(PlantUnit, location_id)
    if unit is None or not unit.is_active:
        raise HTTPException(status_code=400, detail="Unknown or inactive location")
    return unit


def default_shop_id(session: Session) -> Optional[UUID]:
    """The Shop, when there is exactly one active shop; otherwise None (unassigned)."""
    ids = [
        row[0]
        for row in session.query(PlantUnit.id)
        .filter(PlantUnit.kind == "shop", PlantUnit.is_active.is_(True))
        .limit(2)
    ]
    return ids[0] if len(ids) == 1 else None


def _level_query(session: Session, location_id, item_id, raw_material_id):
    q = session.query(StockLevel).filter(StockLevel.location_id == location_id)
    if item_id is not None:
        return q.filter(StockLevel.item_id == item_id)
    return q.filter(StockLevel.raw_material_id == raw_material_id)


def apply(
    session: Session,
    location_id: Optional[UUID],
    delta,
    *,
    item_id: Optional[UUID] = None,
    raw_material_id: Optional[UUID] = None,
) -> None:
    """Add delta to the level at a location. No location means nothing to record."""
    if location_id is None:
        return
    level = (
        _level_query(session, location_id, item_id, raw_material_id)
        .with_for_update()
        .first()
    )
    if level is None:
        level = StockLevel(
            location_id=location_id,
            item_id=item_id,
            raw_material_id=raw_material_id,
            quantity=Decimal("0"),
        )
        session.add(level)
    level.quantity = Decimal(str(level.quantity or 0)) + Decimal(str(delta))
    session.flush()


def level_at(
    session: Session,
    location_id: Optional[UUID],
    *,
    item_id: Optional[UUID] = None,
    raw_material_id: Optional[UUID] = None,
) -> Decimal:
    """How much is at a location, or how much is unassigned when location_id is None."""
    if location_id is not None:
        level = _level_query(session, location_id, item_id, raw_material_id).first()
        return Decimal(str(level.quantity)) if level else Decimal("0")
    if item_id is not None:
        total = session.query(Item.current_stock).filter(Item.id == item_id).scalar()
        placed = (
            session.query(func.coalesce(func.sum(StockLevel.quantity), 0))
            .filter(StockLevel.item_id == item_id)
            .scalar()
        )
    else:
        total = (
            session.query(RawMaterial.stock)
            .filter(RawMaterial.id == raw_material_id)
            .scalar()
        )
        placed = (
            session.query(func.coalesce(func.sum(StockLevel.quantity), 0))
            .filter(StockLevel.raw_material_id == raw_material_id)
            .scalar()
        )
    return Decimal(str(total or 0)) - Decimal(str(placed or 0))


def transfer(
    session: Session,
    *,
    item_id: Optional[UUID],
    raw_material_id: Optional[UUID],
    from_location_id: Optional[UUID],
    to_location_id: Optional[UUID],
    quantity,
    user_id: UUID,
    notes: Optional[str] = None,
) -> UUID:
    """
    Move stock between two locations (None = unassigned). Writes two ledger rows that
    share a transfer id, changes the two levels, and leaves the total untouched.
    Raises 400 if the source holds less than the quantity.
    """
    if (item_id is None) == (raw_material_id is None):
        raise HTTPException(
            status_code=400, detail="Give exactly one of item_id or raw_material_id"
        )
    qty = Decimal(str(quantity))
    if qty <= 0:
        raise HTTPException(status_code=400, detail="Quantity must be positive")
    if from_location_id == to_location_id:
        raise HTTPException(status_code=400, detail="From and to are the same place")
    if item_id is not None and qty != qty.to_integral_value():
        raise HTTPException(status_code=400, detail="Items move in whole numbers")

    for loc in (from_location_id, to_location_id):
        if loc is not None:
            get_location(session, loc)

    # Lock the item or material row so two transfers cannot both pass the check below.
    if item_id is not None:
        target = (
            session.query(Item).filter(Item.id == item_id).with_for_update().first()
        )
        what = "Item"
    else:
        target = (
            session.query(RawMaterial)
            .filter(RawMaterial.id == raw_material_id)
            .with_for_update()
            .first()
        )
        what = "Raw material"
    if target is None:
        raise HTTPException(status_code=404, detail=f"{what} not found")

    have = level_at(
        session, from_location_id, item_id=item_id, raw_material_id=raw_material_id
    )
    if have < qty:
        place = "unassigned" if from_location_id is None else "at that location"
        raise HTTPException(
            status_code=400,
            detail=f"Only {have.normalize():f} of '{target.name}' {place}; cannot move {qty.normalize():f}",
        )

    transfer_id = uuid.uuid4()
    for loc, sign, kind in (
        (from_location_id, -1, "transfer_out"),
        (to_location_id, 1, "transfer_in"),
    ):
        apply(
            session,
            loc,
            sign * qty,
            item_id=item_id,
            raw_material_id=raw_material_id,
        )
        session.add(
            InventoryMovement(
                movement_type=kind,
                reference_type="transfer",
                reference_id=transfer_id,
                transfer_id=transfer_id,
                item_id=item_id,
                raw_material_id=raw_material_id,
                location_id=loc,
                quantity=sign * qty,
                notes=notes,
                created_by=user_id,
            )
        )
    session.flush()
    return transfer_id
