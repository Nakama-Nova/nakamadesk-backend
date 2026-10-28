"""
Designs: reusable patterns with their own code (DES-0001).

A design can come from a restoration (it then shows that restoration's 3D files) or be
made fresh. It can build several sculpture products, and a product shows the designs it
came from: the link is many to many and visible from either side
(GET /designs?item_id=...). Visible only to the owner and artisans, like restorations.
"""

from datetime import datetime
from decimal import Decimal
from typing import List, Literal, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, or_

from app.api.restorations import FileOut
from app.db.deps import RESTORATION_ROLES, check_role, get_uow
from app.models.design import DESIGN_STATUSES, Design, DesignProduct
from app.models.item import Item
from app.models.restoration import RestorationFile, RestorationProject
from app.models.user import User
from app.repositories.base import AbstractUnitOfWork
from app.schemas.item import ItemCreate
from app.services import inventory_service, sku_service

router = APIRouter(prefix="/designs", tags=["Designs"])

Status = Literal["draft", "ready", "retired"]
assert set(Status.__args__) == set(DESIGN_STATUSES)


class DesignCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: Optional[str] = Field(default=None, max_length=2000)
    restoration_id: Optional[UUID] = None  # the restoration it was taken from


class DesignUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    description: Optional[str] = Field(default=None, max_length=2000)
    restoration_id: Optional[UUID] = None
    status: Optional[Status] = None


class ProductRef(BaseModel):
    id: UUID
    sku: str
    name: str
    item_type: str
    selling_price: Decimal
    current_stock: int


class RestorationRef(BaseModel):
    id: UUID
    title: str
    temple_name: Optional[str]
    kind: str
    status: str


class DesignOut(BaseModel):
    id: UUID
    sku: str
    name: str
    description: Optional[str]
    status: str
    restoration_id: Optional[UUID]
    restoration_title: Optional[str]
    product_count: int
    created_at: datetime
    updated_at: datetime


class DesignDetail(DesignOut):
    restoration: Optional[RestorationRef]
    products: List[ProductRef]
    files: List[FileOut]  # the 3D files and G-code of the restoration it came from


class ProductLink(BaseModel):
    item_id: UUID


class ProductFromDesign(BaseModel):
    """Make a new sculpture product from a design. Everything is optional."""

    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    selling_price: Decimal = Field(default=Decimal("0"), ge=0)
    wood_type: Optional[str] = Field(default=None, max_length=60)


# ---------------------------------------------------------------- helpers
def _design(uow: AbstractUnitOfWork, design_id: UUID) -> Design:
    design = uow.session.get(Design, design_id)
    if not design:
        raise HTTPException(status_code=404, detail="Design not found")
    return design


def _check_restoration(uow: AbstractUnitOfWork, restoration_id) -> None:
    if restoration_id and not uow.session.get(RestorationProject, restoration_id):
        raise HTTPException(status_code=404, detail="Restoration not found")


def _out(uow: AbstractUnitOfWork, d: Design) -> dict:
    count = (
        uow.session.query(func.count(DesignProduct.item_id))
        .filter(DesignProduct.design_id == d.id)
        .scalar()
    )
    source = (
        uow.session.get(RestorationProject, d.restoration_id)
        if d.restoration_id
        else None
    )
    return {
        "id": d.id,
        "sku": d.sku,
        "name": d.name,
        "description": d.description,
        "status": d.status,
        "restoration_id": d.restoration_id,
        "restoration_title": source.title if source else None,
        "product_count": count,
        "created_at": d.created_at,
        "updated_at": d.updated_at,
    }


def _products(uow: AbstractUnitOfWork, design_id: UUID) -> List[Item]:
    return (
        uow.session.query(Item)
        .join(DesignProduct, DesignProduct.item_id == Item.id)
        .filter(DesignProduct.design_id == design_id)
        .order_by(Item.sku)
        .all()
    )


def _ref(item: Item) -> ProductRef:
    return ProductRef(
        id=item.id,
        sku=item.sku,
        name=item.name,
        item_type=item.item_type or "furniture",
        selling_price=item.selling_price or Decimal("0"),
        current_stock=item.current_stock or 0,
    )


def _link(uow: AbstractUnitOfWork, design_id: UUID, item_id: UUID) -> bool:
    """Link a design and a product. Returns False when they already were."""
    exists = uow.session.get(DesignProduct, (design_id, item_id))
    if exists:
        return False
    uow.session.add(DesignProduct(design_id=design_id, item_id=item_id))
    return True


# ---------------------------------------------------------------- routes
@router.post("", response_model=DesignOut, status_code=201)
def create_design(
    req: DesignCreate,
    user: User = Depends(check_role(RESTORATION_ROLES)),
    uow: AbstractUnitOfWork = Depends(get_uow),
):
    _check_restoration(uow, req.restoration_id)
    with uow:
        design = Design(
            sku=sku_service.next_design_sku(uow.session),
            name=req.name.strip(),
            description=req.description,
            restoration_id=req.restoration_id,
            created_by=user.id,
        )
        uow.session.add(design)
        uow.commit()
    uow.refresh(design)
    return _out(uow, design)


@router.get("", response_model=List[DesignOut])
def list_designs(
    status: Optional[Status] = Query(default=None),
    restoration_id: Optional[UUID] = Query(default=None),
    item_id: Optional[UUID] = Query(
        default=None, description="designs this product is built from"
    ),
    search: Optional[str] = Query(
        default=None, description="code or name contains this"
    ),
    user: User = Depends(check_role(RESTORATION_ROLES)),
    uow: AbstractUnitOfWork = Depends(get_uow),
):
    query = uow.session.query(Design)
    if status:
        query = query.filter(Design.status == status)
    if restoration_id:
        query = query.filter(Design.restoration_id == restoration_id)
    if item_id:
        query = query.join(DesignProduct, DesignProduct.design_id == Design.id).filter(
            DesignProduct.item_id == item_id
        )
    if search and search.strip():
        like = f"%{search.strip()}%"
        query = query.filter(or_(Design.sku.ilike(like), Design.name.ilike(like)))
    return [_out(uow, d) for d in query.order_by(Design.created_at.desc()).all()]


@router.get("/{design_id}", response_model=DesignDetail)
def get_design(
    design_id: UUID,
    user: User = Depends(check_role(RESTORATION_ROLES)),
    uow: AbstractUnitOfWork = Depends(get_uow),
):
    design = _design(uow, design_id)
    source = (
        uow.session.get(RestorationProject, design.restoration_id)
        if design.restoration_id
        else None
    )
    files = []
    if source:
        files = (
            uow.session.query(RestorationFile)
            .filter(RestorationFile.restoration_id == source.id)
            .order_by(RestorationFile.created_at.desc())
            .all()
        )
    return {
        **_out(uow, design),
        "restoration": (
            RestorationRef(
                id=source.id,
                title=source.title,
                temple_name=source.temple_name,
                kind=source.kind,
                status=source.status,
            )
            if source
            else None
        ),
        "products": [_ref(i) for i in _products(uow, design.id)],
        "files": [FileOut.model_validate(f, from_attributes=True) for f in files],
    }


@router.patch("/{design_id}", response_model=DesignOut)
def update_design(
    design_id: UUID,
    req: DesignUpdate,
    user: User = Depends(check_role(RESTORATION_ROLES)),
    uow: AbstractUnitOfWork = Depends(get_uow),
):
    design = _design(uow, design_id)
    changes = req.model_dump(exclude_unset=True)
    for required in ("name", "status"):
        if required in changes and changes[required] is None:
            raise HTTPException(status_code=422, detail=f"{required} cannot be empty")
    if "restoration_id" in changes:
        _check_restoration(uow, changes["restoration_id"])
    for key, value in changes.items():
        setattr(design, key, value)
    uow.commit()
    uow.refresh(design)
    return _out(uow, design)


@router.post("/{design_id}/products", response_model=List[ProductRef])
def link_product(
    design_id: UUID,
    req: ProductLink,
    user: User = Depends(check_role(RESTORATION_ROLES)),
    uow: AbstractUnitOfWork = Depends(get_uow),
):
    """Say that a product is built from this design. Linking twice changes nothing."""
    design = _design(uow, design_id)
    if not uow.session.get(Item, req.item_id):
        raise HTTPException(status_code=404, detail="Product not found")
    _link(uow, design.id, req.item_id)
    uow.commit()
    return [_ref(i) for i in _products(uow, design.id)]


@router.delete("/{design_id}/products/{item_id}", response_model=List[ProductRef])
def unlink_product(
    design_id: UUID,
    item_id: UUID,
    user: User = Depends(check_role(RESTORATION_ROLES)),
    uow: AbstractUnitOfWork = Depends(get_uow),
):
    design = _design(uow, design_id)
    link = uow.session.get(DesignProduct, (design.id, item_id))
    if not link:
        raise HTTPException(
            status_code=404, detail="That product is not linked to this design"
        )
    uow.session.delete(link)
    uow.commit()
    return [_ref(i) for i in _products(uow, design.id)]


@router.post("/{design_id}/create-product", response_model=ProductRef, status_code=201)
def create_product_from_design(
    design_id: UUID,
    req: ProductFromDesign,
    user: User = Depends(check_role(RESTORATION_ROLES)),
    uow: AbstractUnitOfWork = Depends(get_uow),
):
    """
    Build a product out of this design: a new sculpture in stock with its own code
    (SCU-0001), named after the design unless you give another name, and linked to it.
    """
    design = _design(uow, design_id)
    item = inventory_service.create_item(
        uow,
        ItemCreate(
            name=(req.name or design.name).strip(),
            description=design.description,
            item_type="sculpture",
            selling_price=req.selling_price,
            wood_type=req.wood_type,
        ),
    )
    _link(uow, design.id, item.id)
    uow.commit()
    uow.refresh(item)
    return _ref(item)
