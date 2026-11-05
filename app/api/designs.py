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

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func, or_

from app.api import scans as scans_api
from app.api.restorations import FileOut
from app.db.deps import RESTORATION_ROLES, check_role, get_uow
from app.models.design import (
    DESIGN_CATEGORIES,
    DESIGN_STATUSES,
    Design,
    DesignProduct,
)
from app.models.furniture_carving import FurnitureCarving
from app.models.furniture_part import FurniturePart
from app.models.item import Item
from app.models.product_asset import ProductAsset
from app.models.restoration import RestorationFile, RestorationProject
from app.models.user import User
from app.repositories.base import AbstractUnitOfWork
from app.schemas.item import ItemCreate
from app.services import design_service, inventory_service, media_service, sku_service
from app.services.object_store import S3Store, get_store_or_none

router = APIRouter(prefix="/designs", tags=["Designs"])

Status = Literal["draft", "ready", "retired"]
assert set(Status.__args__) == set(DESIGN_STATUSES)
Category = Literal["general", "furniture"]
assert set(Category.__args__) == set(DESIGN_CATEGORIES)


class DesignCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: Optional[str] = Field(default=None, max_length=2000)
    restoration_id: Optional[UUID] = None  # the restoration it was taken from
    category: Category = "general"


class DesignUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    description: Optional[str] = Field(default=None, max_length=2000)
    restoration_id: Optional[UUID] = None
    status: Optional[Status] = None
    category: Optional[Category] = None


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
    category: str
    has_image: bool
    scan_id: Optional[UUID]  # the flat-carving scan it was made from, if any
    restoration_id: Optional[UUID]
    restoration_title: Optional[str]
    product_count: int
    created_at: datetime
    updated_at: datetime


class FurnitureUse(BaseModel):
    """Where a design is carved: one row per placement (the design itself is stored once)."""

    item_id: UUID
    item_name: str
    part_name: Optional[str]
    face: str


class ScanFileOut(BaseModel):
    """A result of the scan a design was made from (G-code, 3D files, depth picture)."""

    id: UUID
    kind: str
    content_type: str
    bytes: int
    alt: Optional[str]
    download_path: (
        str  # GET it with the same sign-in: it answers with a short-lived link
    )


class FromScan(BaseModel):
    scan_id: UUID
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)


class FromScanOut(DesignOut):
    created: bool  # False when the scan (or its picture) already was a design


class DesignDetail(DesignOut):
    restoration: Optional[RestorationRef]
    products: List[ProductRef]
    files: List[FileOut]  # the 3D files and G-code of the restoration it came from
    furniture: List[FurnitureUse]  # the furniture parts it is carved on (3D Lab)
    scan_files: List[ScanFileOut]  # files of the scan it was made from


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
        "category": d.category,
        "has_image": bool(d.image_key),
        "scan_id": d.scan_id,
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


def _scan_files(uow: AbstractUnitOfWork, design: Design) -> List[ScanFileOut]:
    if not design.scan_id:
        return []
    rows = (
        uow.session.query(ProductAsset)
        .filter(ProductAsset.scan_id == design.scan_id)
        .order_by(ProductAsset.sort, ProductAsset.created_at)
        .all()
    )
    return [
        ScanFileOut(
            id=a.id,
            kind=a.kind,
            content_type=a.content_type,
            bytes=a.bytes,
            alt=a.alt,
            download_path=f"/items/{a.item_id}/assets/{a.id}/download",
        )
        for a in rows
    ]


def _furniture_use(uow: AbstractUnitOfWork, design_id: UUID) -> List[FurnitureUse]:
    rows = (
        uow.session.query(FurnitureCarving, Item.name, FurniturePart.name)
        .join(Item, Item.id == FurnitureCarving.item_id)
        .outerjoin(FurniturePart, FurniturePart.id == FurnitureCarving.part_id)
        .filter(FurnitureCarving.design_id == design_id)
        .order_by(Item.name, FurnitureCarving.sort)
        .all()
    )
    return [
        FurnitureUse(item_id=c.item_id, item_name=item, part_name=part, face=c.face)
        for c, item, part in rows
    ]


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
            category=req.category,
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
    category: Optional[Category] = Query(default=None),
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
    if category:
        query = query.filter(Design.category == category)
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
        "furniture": _furniture_use(uow, design.id),
        "scan_files": _scan_files(uow, design),
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
    for required in ("name", "status", "category"):
        if required in changes and changes[required] is None:
            raise HTTPException(status_code=422, detail=f"{required} cannot be empty")
    if "restoration_id" in changes:
        _check_restoration(uow, changes["restoration_id"])
    for key, value in changes.items():
        setattr(design, key, value)
    uow.commit()
    uow.refresh(design)
    return _out(uow, design)


@router.post("/from-scan", response_model=FromScanOut)
def create_design_from_scan(
    req: FromScan,
    user: User = Depends(check_role(RESTORATION_ROLES)),
    uow: AbstractUnitOfWork = Depends(get_uow),
    store: Optional[S3Store] = Depends(get_store_or_none),
):
    """
    On request: make a furniture design from a finished flat-carving scan. The scan's
    depth picture becomes the design's picture, and its G-code and 3D files stay linked
    (GET /designs/{id} lists them). Asking again for the same scan, or for a scan whose
    picture is already a design, returns that design with created = false.
    """
    job = scans_api._get(uow, req.scan_id)
    scans_api._visible(job, user)
    design, created = design_service.design_from_scan(uow, job, user, req.name, store)
    uow.refresh(design)
    return {**_out(uow, design), "created": created}


@router.put("/{design_id}/image", response_model=DesignOut)
async def set_design_image(
    design_id: UUID,
    file: UploadFile = File(...),
    user: User = Depends(check_role(RESTORATION_ROLES)),
    uow: AbstractUnitOfWork = Depends(get_uow),
    store: Optional[S3Store] = Depends(get_store_or_none),
):
    """
    The design's picture (JPG, PNG or WebP, 10 MB max), shown on furniture in the 3D
    Lab. Private. The same picture cannot be stored under two designs (409 names the
    first one): reuse that design and place it more than once instead.
    """
    design = _design(uow, design_id)
    data = await file.read(media_service.max_read_size())
    design_service.store_image(uow, design, data, file.content_type or "", store)
    uow.refresh(design)
    return _out(uow, design)


@router.get("/{design_id}/image")
def get_design_image(
    design_id: UUID,
    user: User = Depends(check_role(RESTORATION_ROLES)),
    uow: AbstractUnitOfWork = Depends(get_uow),
    store: Optional[S3Store] = Depends(get_store_or_none),
):
    return design_service.image_response(_design(uow, design_id), store)


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
