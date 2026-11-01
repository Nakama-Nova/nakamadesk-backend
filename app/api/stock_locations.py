"""
Stock by location and moves between locations. Owner and manager move stock; sales
staff can look. Totals stay on the items and raw materials as before; see
app/services/stock_location_service.py for the rules.
"""

from decimal import Decimal
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import func

from app.db.deps import check_role, get_uow
from app.models.enums import UserRole
from app.models.inventory_movement import InventoryMovement
from app.models.item import Item
from app.models.plant_unit import PlantUnit
from app.models.raw_material import RawMaterial
from app.models.stock_level import StockLevel
from app.models.user import User
from app.repositories.base import AbstractUnitOfWork
from app.services import stock_location_service

router = APIRouter(prefix="/stock", tags=["Stock by location"])

MOVERS = [UserRole.OWNER, UserRole.MANAGER]
VIEWERS = [UserRole.OWNER, UserRole.MANAGER, UserRole.SALES]


class LevelOut(BaseModel):
    location_id: Optional[UUID]  # empty = not placed at a location yet
    location_name: Optional[str]
    item_id: Optional[UUID] = None
    raw_material_id: Optional[UUID] = None
    name: str
    quantity: Decimal


class TransferIn(BaseModel):
    item_id: Optional[UUID] = None
    raw_material_id: Optional[UUID] = None
    # null on either side means "not placed at a location yet"
    from_location_id: Optional[UUID] = None
    to_location_id: Optional[UUID] = None
    quantity: Decimal = Field(gt=0, max_digits=12, decimal_places=4)
    notes: Optional[str] = Field(default=None, max_length=300)

    @model_validator(mode="after")
    def one_thing_two_places(self):
        if (self.item_id is None) == (self.raw_material_id is None):
            raise ValueError("give exactly one of item_id or raw_material_id")
        if self.from_location_id is None and self.to_location_id is None:
            raise ValueError("give a from_location_id, a to_location_id, or both")
        return self


class TransferOut(BaseModel):
    transfer_id: UUID
    from_quantity: Decimal  # what is left where it came from
    to_quantity: Decimal  # what is now where it went


@router.get("/levels", response_model=List[LevelOut])
def levels(
    location_id: Optional[UUID] = None,
    item_id: Optional[UUID] = None,
    raw_material_id: Optional[UUID] = None,
    limit: int = Query(default=200, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    _: User = Depends(check_role(VIEWERS)),
    uow: AbstractUnitOfWork = Depends(get_uow),
):
    """What is at each location. Add `/stock/unassigned` for what has no location yet."""
    q = uow.session.query(StockLevel, PlantUnit.name).join(
        PlantUnit, PlantUnit.id == StockLevel.location_id
    )
    if location_id:
        q = q.filter(StockLevel.location_id == location_id)
    if item_id:
        q = q.filter(StockLevel.item_id == item_id)
    if raw_material_id:
        q = q.filter(StockLevel.raw_material_id == raw_material_id)
    rows = q.order_by(PlantUnit.name, StockLevel.id).limit(limit).offset(offset).all()
    item_names = dict(
        uow.session.query(Item.id, Item.name)
        .filter(Item.id.in_([r.item_id for r, _ in rows if r.item_id]))
        .all()
    )
    material_names = dict(
        uow.session.query(RawMaterial.id, RawMaterial.name)
        .filter(
            RawMaterial.id.in_(
                [r.raw_material_id for r, _ in rows if r.raw_material_id]
            )
        )
        .all()
    )
    return [
        LevelOut(
            location_id=r.location_id,
            location_name=loc_name,
            item_id=r.item_id,
            raw_material_id=r.raw_material_id,
            name=item_names.get(r.item_id) or material_names.get(r.raw_material_id),
            quantity=r.quantity,
        )
        for r, loc_name in rows
    ]


@router.get("/unassigned", response_model=List[LevelOut])
def unassigned(
    limit: int = Query(default=200, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    _: User = Depends(check_role(VIEWERS)),
    uow: AbstractUnitOfWork = Depends(get_uow),
):
    """Stock that exists but is not at any location yet (total minus the levels)."""
    s = uow.session
    placed_items = (
        s.query(StockLevel.item_id, func.sum(StockLevel.quantity).label("q"))
        .filter(StockLevel.item_id.isnot(None))
        .group_by(StockLevel.item_id)
        .subquery()
    )
    placed_materials = (
        s.query(StockLevel.raw_material_id, func.sum(StockLevel.quantity).label("q"))
        .filter(StockLevel.raw_material_id.isnot(None))
        .group_by(StockLevel.raw_material_id)
        .subquery()
    )
    item_left = Item.current_stock - func.coalesce(placed_items.c.q, 0)
    material_left = RawMaterial.stock - func.coalesce(placed_materials.c.q, 0)
    items = (
        s.query(Item.id, Item.name, item_left)
        .outerjoin(placed_items, placed_items.c.item_id == Item.id)
        .filter(item_left != 0)
        .order_by(Item.name)
        .limit(limit)
        .offset(offset)
        .all()
    )
    materials = (
        s.query(RawMaterial.id, RawMaterial.name, material_left)
        .outerjoin(
            placed_materials, placed_materials.c.raw_material_id == RawMaterial.id
        )
        .filter(material_left != 0)
        .order_by(RawMaterial.name)
        .limit(limit)
        .offset(offset)
        .all()
    )
    return [
        LevelOut(
            location_id=None, location_name=None, item_id=i, name=n, quantity=Decimal(q)
        )
        for i, n, q in items
    ] + [
        LevelOut(
            location_id=None,
            location_name=None,
            raw_material_id=m,
            name=n,
            quantity=Decimal(q),
        )
        for m, n, q in materials
    ]


@router.post("/transfers", response_model=TransferOut, status_code=201)
def create_transfer(
    body: TransferIn,
    current_user: User = Depends(check_role(MOVERS)),
    uow: AbstractUnitOfWork = Depends(get_uow),
):
    """
    Move stock from one location to another (null = not placed at a location yet).
    Moving it out of "unassigned" into a location is how existing stock gets placed.
    The total on the item or material does not change.
    """
    with uow:
        transfer_id = stock_location_service.transfer(
            uow.session,
            item_id=body.item_id,
            raw_material_id=body.raw_material_id,
            from_location_id=body.from_location_id,
            to_location_id=body.to_location_id,
            quantity=body.quantity,
            user_id=current_user.id,
            notes=body.notes,
        )
        uow.commit()
    key = dict(item_id=body.item_id, raw_material_id=body.raw_material_id)
    return TransferOut(
        transfer_id=transfer_id,
        from_quantity=stock_location_service.level_at(
            uow.session, body.from_location_id, **key
        ),
        to_quantity=stock_location_service.level_at(
            uow.session, body.to_location_id, **key
        ),
    )


class TransferRow(BaseModel):
    transfer_id: UUID
    created_at: str
    item_id: Optional[UUID]
    raw_material_id: Optional[UUID]
    from_location_id: Optional[UUID]
    to_location_id: Optional[UUID]
    quantity: Decimal
    notes: Optional[str]


@router.get("/transfers", response_model=List[TransferRow])
def list_transfers(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    _: User = Depends(check_role(VIEWERS)),
    uow: AbstractUnitOfWork = Depends(get_uow),
):
    """Recent transfers, newest first."""
    out_rows = (
        uow.session.query(InventoryMovement)
        .filter(InventoryMovement.movement_type == "transfer_out")
        .order_by(InventoryMovement.created_at.desc())
        .limit(limit)
        .offset(offset)
        .all()
    )
    ids = [r.transfer_id for r in out_rows]
    in_by_transfer = {
        r.transfer_id: r.location_id
        for r in uow.session.query(InventoryMovement)
        .filter(
            InventoryMovement.movement_type == "transfer_in",
            InventoryMovement.transfer_id.in_(ids),
        )
        .all()
    }
    return [
        TransferRow(
            transfer_id=r.transfer_id,
            created_at=r.created_at.isoformat(),
            item_id=r.item_id,
            raw_material_id=r.raw_material_id,
            from_location_id=r.location_id,
            to_location_id=in_by_transfer.get(r.transfer_id),
            quantity=-r.quantity,
            notes=r.notes,
        )
        for r in out_rows
    ]
