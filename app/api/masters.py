"""
Master data the stock screens need: suppliers, categories and brands, plus the
stock movement history. All owner/manager; deleting a supplier is owner-only
and nothing that is still in use can be deleted (409 with a clear reason).
"""

from datetime import datetime
from decimal import Decimal
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import IntegrityError

from app.db.deps import check_role, get_uow
from app.models.brand import Brand
from app.models.category import Category
from app.models.enums import UserRole
from app.models.inventory_movement import InventoryMovement
from app.models.item import Item
from app.models.purchase import Purchase
from app.models.raw_material import RawMaterial
from app.models.supplier import Supplier
from app.models.user import User
from app.repositories.base import AbstractUnitOfWork

STAFF = [UserRole.OWNER, UserRole.MANAGER]

suppliers = APIRouter(prefix="/suppliers", tags=["Suppliers"])
categories = APIRouter(prefix="/categories", tags=["Categories"])
brands = APIRouter(prefix="/brands", tags=["Brands"])
inventory = APIRouter(prefix="/inventory", tags=["Inventory"])


# ----------------------------------------------------------------- schemas
class NameIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: Optional[str] = Field(default=None, max_length=300)


class NameOut(NameIn):
    id: UUID
    model_config = ConfigDict(from_attributes=True)


class SupplierIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    contact_person: Optional[str] = Field(default=None, max_length=120)
    phone: Optional[str] = Field(default=None, max_length=30)
    email: Optional[str] = Field(default=None, max_length=120)
    address: Optional[str] = Field(default=None, max_length=300)
    gstin: Optional[str] = Field(default=None, max_length=20)


class SupplierOut(SupplierIn):
    id: UUID
    created_at: Optional[datetime] = None
    model_config = ConfigDict(from_attributes=True)


class MovementOut(BaseModel):
    id: UUID
    created_at: datetime
    movement_type: str
    reference_type: Optional[str]
    quantity: Decimal
    unit_cost: Optional[Decimal]
    notes: Optional[str]
    item_id: Optional[UUID]
    raw_material_id: Optional[UUID]
    name: Optional[str]  # the item or material this moved
    by: Optional[str]  # username


# ----------------------------------------------------------------- helpers
def _get_or_404(uow: AbstractUnitOfWork, model, id: UUID, label: str):
    row = uow.session.get(model, id)
    if not row:
        raise HTTPException(status_code=404, detail=f"{label} not found")
    return row


def _save(uow: AbstractUnitOfWork, row, duplicate_message: str):
    try:
        uow.session.add(row)
        uow.commit()
    except IntegrityError:
        uow.session.rollback()
        raise HTTPException(status_code=409, detail=duplicate_message) from None
    uow.refresh(row)
    return row


def _delete(uow: AbstractUnitOfWork, row, in_use: bool, label: str, why: str):
    if in_use:
        raise HTTPException(
            status_code=409, detail=f"{label} is {why}; it cannot be deleted"
        )
    uow.session.delete(row)
    uow.commit()
    return {"message": f"{label} deleted"}


def _used(uow: AbstractUnitOfWork, model, column, id: UUID) -> bool:
    return uow.session.query(model).filter(column == id).first() is not None


# ----------------------------------------------------------------- suppliers
@suppliers.get("/", response_model=List[SupplierOut])
def list_suppliers(
    _: User = Depends(check_role(STAFF)), uow: AbstractUnitOfWork = Depends(get_uow)
):
    return uow.session.query(Supplier).order_by(Supplier.name).all()


@suppliers.post("/", response_model=SupplierOut, status_code=201)
def create_supplier(
    body: SupplierIn,
    _: User = Depends(check_role(STAFF)),
    uow: AbstractUnitOfWork = Depends(get_uow),
):
    return _save(uow, Supplier(**body.model_dump()), "Supplier already exists")


@suppliers.put("/{supplier_id}", response_model=SupplierOut)
def update_supplier(
    supplier_id: UUID,
    body: SupplierIn,
    _: User = Depends(check_role(STAFF)),
    uow: AbstractUnitOfWork = Depends(get_uow),
):
    row = _get_or_404(uow, Supplier, supplier_id, "Supplier")
    for k, v in body.model_dump().items():
        setattr(row, k, v)
    return _save(uow, row, "Supplier already exists")


@suppliers.delete("/{supplier_id}")
def delete_supplier(
    supplier_id: UUID,
    _: User = Depends(check_role([UserRole.OWNER])),
    uow: AbstractUnitOfWork = Depends(get_uow),
):
    row = _get_or_404(uow, Supplier, supplier_id, "Supplier")
    used = _used(uow, Purchase, Purchase.supplier_id, supplier_id)
    return _delete(uow, row, used, "Supplier", "on past purchases")


# ------------------------------------------------- categories and brands
def _taxonomy(router: APIRouter, model, item_column, label: str, plural: str):
    @router.get("/", response_model=List[NameOut], name=f"list_{plural}")
    def list_rows(
        _: User = Depends(check_role(STAFF)),
        uow: AbstractUnitOfWork = Depends(get_uow),
    ):
        return uow.session.query(model).order_by(model.name).all()

    @router.post("/", response_model=NameOut, status_code=201, name=f"create_{plural}")
    def create_row(
        body: NameIn,
        _: User = Depends(check_role(STAFF)),
        uow: AbstractUnitOfWork = Depends(get_uow),
    ):
        return _save(uow, model(**body.model_dump()), f"{label} already exists")

    @router.put("/{row_id}", response_model=NameOut, name=f"update_{plural}")
    def update_row(
        row_id: UUID,
        body: NameIn,
        _: User = Depends(check_role(STAFF)),
        uow: AbstractUnitOfWork = Depends(get_uow),
    ):
        row = _get_or_404(uow, model, row_id, label)
        for k, v in body.model_dump().items():
            setattr(row, k, v)
        return _save(uow, row, f"{label} already exists")

    @router.delete("/{row_id}", name=f"delete_{plural}")
    def delete_row(
        row_id: UUID,
        _: User = Depends(check_role(STAFF)),
        uow: AbstractUnitOfWork = Depends(get_uow),
    ):
        row = _get_or_404(uow, model, row_id, label)
        used = _used(uow, Item, item_column, row_id)
        return _delete(uow, row, used, label, "used by products")


_taxonomy(categories, Category, Item.category_id, "Category", "categories")
_taxonomy(brands, Brand, Item.brand_id, "Brand", "brands")


# ----------------------------------------------------- movement history
@inventory.get("/movements", response_model=List[MovementOut])
def movements(
    item_id: Optional[UUID] = None,
    raw_material_id: Optional[UUID] = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    _: User = Depends(check_role(STAFF)),
    uow: AbstractUnitOfWork = Depends(get_uow),
):
    """The stock ledger, newest first: every receipt, sale, use and manual change."""
    q = uow.session.query(InventoryMovement)
    if item_id:
        q = q.filter(InventoryMovement.item_id == item_id)
    if raw_material_id:
        q = q.filter(InventoryMovement.raw_material_id == raw_material_id)
    rows = (
        q.order_by(InventoryMovement.created_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    item_names = {
        i.id: i.name
        for i in uow.session.query(Item.id, Item.name)
        .filter(Item.id.in_([r.item_id for r in rows if r.item_id]))
        .all()
    }
    material_names = {
        m.id: m.name
        for m in uow.session.query(RawMaterial.id, RawMaterial.name)
        .filter(
            RawMaterial.id.in_([r.raw_material_id for r in rows if r.raw_material_id])
        )
        .all()
    }
    users = {
        u.id: u.username
        for u in uow.session.query(User.id, User.username)
        .filter(User.id.in_([r.created_by for r in rows if r.created_by]))
        .all()
    }
    return [
        MovementOut(
            id=r.id,
            created_at=r.created_at,
            movement_type=r.movement_type,
            reference_type=r.reference_type,
            quantity=r.quantity,
            unit_cost=r.unit_cost,
            notes=r.notes,
            item_id=r.item_id,
            raw_material_id=r.raw_material_id,
            name=item_names.get(r.item_id) or material_names.get(r.raw_material_id),
            by=users.get(r.created_by),
        )
        for r in rows
    ]
