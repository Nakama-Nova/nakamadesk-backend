"""
The 3D Lab (staff only): parts of a furniture piece, solved for a size.

Everyone in the workshop (owner, manager, achari, worker) can VIEW. Only the
owner and managers can change part definitions. Viewing never changes data:
picking Small/Medium/Large or typing a custom size just recalculates.
"""

import re
from pathlib import Path
from typing import Dict, List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy import func

from app.core.security import create_scoped_token, verify_token
from app.db.deps import (
    LAB_COOKIE,
    LAB_EDIT_ROLES,
    check_token_version,
    get_lab_editor,
    get_lab_user,
    get_uow,
)
from app.models.design import Design
from app.models.furniture_carving import FurnitureCarving
from app.models.furniture_hardware import FurnitureHardware
from app.models.furniture_part import FurniturePart
from app.models.item import Item
from app.models.product_asset import ProductAsset
from app.models.raw_material import RawMaterial
from app.models.user import User
from app.repositories.base import AbstractUnitOfWork
from app.schemas.lab import (
    CarvingListIn,
    CarvingOut,
    FurnitureIn,
    HardwareListIn,
    HardwareOut,
    LabItem,
    LinkIn,
    PartOut,
    PartsIn,
    PresetsIn,
    SizeIn,
)
from app.services import design_service, lab_service, media_service
from app.services.object_store import S3Store, get_store_or_none

router = APIRouter(prefix="/lab", tags=["3D Lab"])
STATIC_DIR = Path(__file__).resolve().parent.parent / "static" / "lab"
LINK_MINUTES, SESSION_MINUTES = 10, 30

PAGE_HEADERS = {
    "Cache-Control": "no-store",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    # own scripts/styles only; data from this server; models may come from the store
    "Content-Security-Policy": (
        "default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; "
        "connect-src 'self' https:; frame-ancestors 'none'"
    ),
}


# ------------------------------------------------------------------ helpers
def _item(uow: AbstractUnitOfWork, item_id: UUID) -> Item:
    item = uow.items.get_by_id(item_id)
    if not item or not item.is_active:
        raise HTTPException(status_code=404, detail="Item not found")
    return item


def _furniture(uow: AbstractUnitOfWork, item_id: UUID) -> Item:
    item = _item(uow, item_id)
    if not item.is_furniture:
        raise HTTPException(
            status_code=404, detail="This item is not set up as furniture"
        )
    return item


def _parts(uow: AbstractUnitOfWork, item_id: UUID) -> List[FurniturePart]:
    return (
        uow.session.query(FurniturePart)
        .filter(FurniturePart.item_id == item_id)
        .order_by(FurniturePart.assembly_step, FurniturePart.sort)
        .all()
    )


def _hardware(uow: AbstractUnitOfWork, item_id: UUID) -> List[FurnitureHardware]:
    return (
        uow.session.query(FurnitureHardware)
        .filter(FurnitureHardware.item_id == item_id)
        .order_by(FurnitureHardware.sort)
        .all()
    )


def _carvings(uow: AbstractUnitOfWork, item_id: UUID) -> List[FurnitureCarving]:
    return (
        uow.session.query(FurnitureCarving)
        .filter(FurnitureCarving.item_id == item_id)
        .order_by(FurnitureCarving.sort)
        .all()
    )


def _designs(uow: AbstractUnitOfWork, ids) -> Dict[UUID, Design]:
    ids = list(ids)
    if not ids:
        return {}
    return {d.id: d for d in uow.session.query(Design).filter(Design.id.in_(ids)).all()}


def _check_all(uow: AbstractUnitOfWork, item: Item) -> None:
    """After a size change: parts, hardware AND carving margins must still work at every size."""
    parts = _parts(uow, item.id)
    lab_service.check_definition(item, parts)
    lab_service.check_hardware(item, _hardware(uow, item.id), parts)
    carvings = _carvings(uow, item.id)
    designs = _designs(uow, {c.design_id for c in carvings})
    lab_service.check_carvings(item, carvings, parts, designs)


def _solved(uow: AbstractUnitOfWork, item: Item, **size) -> dict:
    carvings = _carvings(uow, item.id)
    return lab_service.solve(
        item,
        _parts(uow, item.id),
        hardware=_hardware(uow, item.id),
        carvings=carvings,
        designs=_designs(uow, {c.design_id for c in carvings}),
        **size,
    )


# ------------------------------------------------------------------ read (everyone in the workshop)
@router.get("/items", response_model=List[LabItem])
def list_furniture(
    user: User = Depends(get_lab_user), uow: AbstractUnitOfWork = Depends(get_uow)
):
    counts = dict(
        uow.session.query(FurniturePart.item_id, func.count(FurniturePart.id))
        .group_by(FurniturePart.item_id)
        .all()
    )
    items = (
        uow.session.query(Item)
        .filter(Item.is_furniture.is_(True), Item.is_active.is_(True))
        .order_by(Item.name)
        .all()
    )
    return [
        LabItem(
            id=i.id,
            name=i.name,
            sku=i.sku,
            parts=counts.get(i.id, 0),
            sizes=list(lab_service.presets(i)),
        )
        for i in items
    ]


@router.get("/items/{item_id}")
def solve_item(
    item_id: UUID,
    size: Optional[str] = Query(None, pattern="^[SMLsml]$", description="S, M or L"),
    l: Optional[float] = Query(None, description="custom length, mm"),  # noqa: E741
    w: Optional[float] = Query(None, description="custom width, mm"),
    h: Optional[float] = Query(None, description="custom height, mm"),
    user: User = Depends(get_lab_user),
    uow: AbstractUnitOfWork = Depends(get_uow),
):
    """The parts, positions and cutting list for a size. Read-only: nothing is saved."""
    item = _furniture(uow, item_id)
    return _solved(uow, item, size=size, l=l, w=w, h=h)


@router.get("/designs/{design_id}/image")
def design_image(
    design_id: UUID,
    user: User = Depends(get_lab_user),
    uow: AbstractUnitOfWork = Depends(get_uow),
    store: Optional[S3Store] = Depends(get_store_or_none),
):
    """
    The picture of a design that is carved on some furniture, for the viewer. Only
    designs placed in the Lab are served here; the rest of the library stays
    owner and artisan only.
    """
    carved = (
        uow.session.query(FurnitureCarving.id)
        .filter(FurnitureCarving.design_id == design_id)
        .first()
    )
    design = uow.session.get(Design, design_id) if carved else None
    return design_service.image_response(design, store)


@router.get("/parts/{part_id}/model")
def part_model(
    part_id: UUID,
    user: User = Depends(get_lab_user),
    uow: AbstractUnitOfWork = Depends(get_uow),
    store: Optional[S3Store] = Depends(get_store_or_none),
):
    """Signed link to a carved part's .glb (the viewer loads it)."""
    part = uow.session.query(FurniturePart).filter(FurniturePart.id == part_id).first()
    asset = (
        uow.session.query(ProductAsset)
        .filter(ProductAsset.id == part.model_asset_id)
        .first()
        if part and part.model_asset_id
        else None
    )
    if not asset:
        raise HTTPException(status_code=404, detail="No model for this part")
    if asset.storage in media_service.MEDIA_STORAGES:
        return RedirectResponse(f"/media/{asset.storage_key}")
    if store is None:
        raise HTTPException(status_code=503, detail="File storage is not set up yet")
    return RedirectResponse(store.presign_get(asset.storage_key, filename="part.glb"))


# ------------------------------------------------------------------ edit (owner, manager)
@router.get("/items/{item_id}/definition")
def get_definition(
    item_id: UUID,
    user: User = Depends(get_lab_editor),
    uow: AbstractUnitOfWork = Depends(get_uow),
):
    """The raw formulas, for the editor page."""
    item = _item(uow, item_id)
    return {
        "item": {
            "id": str(item.id),
            "name": item.name,
            "is_furniture": item.is_furniture,
        },
        "dimensions_mm": item.dimensions_mm,
        "presets": lab_service.presets(item),
        "parts": [
            PartOut.model_validate(p).model_dump(mode="json")
            for p in _parts(uow, item_id)
        ],
        # every line, clamps included (kind "clamp"): this is what PUT .../hardware replaces
        "hardware": [
            HardwareOut.model_validate(h).model_dump(mode="json")
            for h in _hardware(uow, item_id)
        ],
        "carvings": [
            CarvingOut.model_validate(c).model_dump(mode="json")
            for c in _carvings(uow, item_id)
        ],
        # the CNC designs an editor may carve (code and name only; designs stay owner/artisan)
        "designs": [
            {
                "id": str(d.id),
                "sku": d.sku,
                "name": d.name,
                "category": d.category,
                "has_image": bool(d.image_key),
            }
            for d in uow.session.query(Design)
            .filter(Design.status != "retired")
            .order_by(Design.sku)
            .all()
        ],
    }


@router.put("/items/{item_id}/parts")
def replace_parts(
    item_id: UUID,
    body: PartsIn,
    user: User = Depends(get_lab_editor),
    uow: AbstractUnitOfWork = Depends(get_uow),
):
    """Replace the whole part list. Rejected unless every formula works for every size."""
    item = _item(uow, item_id)
    new_parts = []
    kept = {p.id for p in _parts(uow, item_id)}
    seen = set()
    for n, p in enumerate(body.parts):
        if p.id and (p.id not in kept or p.id in seen):
            raise HTTPException(
                status_code=422, detail=f"{p.name}: id is not a part of this item"
            )
        if p.id:
            seen.add(p.id)
        if p.raw_material_id and not uow.session.get(RawMaterial, p.raw_material_id):
            raise HTTPException(
                status_code=422, detail=f"{p.name}: raw material not found"
            )
        if p.model_asset_id:
            asset = uow.session.get(ProductAsset, p.model_asset_id)
            if (
                not asset
                or asset.item_id != item_id
                or asset.kind not in ("part_model", "glb")
            ):
                raise HTTPException(
                    status_code=422,
                    detail=f"{p.name}: model must be a part_model file of this item",
                )
        new_parts.append(FurniturePart(item_id=item_id, sort=n, **p.model_dump()))
    lab_service.check_definition(
        item, new_parts
    )  # 422 with the part and size on failure
    with uow:
        # Hardware and carvings point at parts by id and the delete below sets those links to
        # NULL, so remember the ones whose part comes back (same id) and restore them.
        links = [
            (model, row.id, row.part_id)
            for model, rows in (
                (FurnitureHardware, _hardware(uow, item_id)),
                (FurnitureCarving, _carvings(uow, item_id)),
            )
            for row in rows
            if row.part_id in seen
        ]
        uow.session.query(FurniturePart).filter(
            FurniturePart.item_id == item_id
        ).delete()
        uow.session.add_all(new_parts)
        uow.session.flush()
        for model, row_id, part_id in links:
            uow.session.query(model).filter(model.id == row_id).update(
                {"part_id": part_id}
            )
        uow.commit()
    uow.session.expire_all()
    return _solved(uow, item)


@router.put("/items/{item_id}/hardware")
def replace_hardware(
    item_id: UUID,
    body: HardwareListIn,
    user: User = Depends(get_lab_editor),
    uow: AbstractUnitOfWork = Depends(get_uow),
):
    """
    Replace the whole hardware list (screws, nails, dowels, clamps...). Rejected
    unless every quantity formula gives 0 or more for every size.
    """
    item = _item(uow, item_id)
    parts = _parts(uow, item_id)
    part_ids = {p.id for p in parts}
    new_lines = []
    for n, h in enumerate(body.hardware):
        if h.part_id and h.part_id not in part_ids:
            raise HTTPException(
                status_code=422,
                detail=f"{item.name}: hardware line {n + 1} ({h.name}): part not found on this item",
            )
        new_lines.append(FurnitureHardware(item_id=item_id, sort=n, **h.model_dump()))
    lab_service.check_hardware(item, new_lines, parts)
    with uow:
        uow.session.query(FurnitureHardware).filter(
            FurnitureHardware.item_id == item_id
        ).delete()
        uow.session.add_all(new_lines)
        uow.commit()
    uow.session.expire_all()
    return _solved(uow, item)


@router.put("/items/{item_id}/carvings")
def replace_carvings(
    item_id: UUID,
    body: CarvingListIn,
    user: User = Depends(get_lab_editor),
    uow: AbstractUnitOfWork = Depends(get_uow),
):
    """
    Replace the whole list of carvings (a design on a part's face). A design is
    stored once in CNC designs and may be placed any number of times, for
    example on both side panels. A design placed here is filed under
    "furniture" in the designs library.
    """
    item = _item(uow, item_id)
    parts = _parts(uow, item_id)
    part_ids = {p.id for p in parts}
    designs = _designs(uow, {c.design_id for c in body.carvings})
    new_rows = []
    for n, c in enumerate(body.carvings, 1):
        if c.part_id not in part_ids:
            raise HTTPException(
                status_code=422,
                detail=f"{item.name}: carving line {n}: part not found on this item",
            )
        design = designs.get(c.design_id)
        if design is None:
            raise HTTPException(
                status_code=422,
                detail=f"{item.name}: carving line {n}: design not found",
            )
        if not design.image_key:
            raise HTTPException(
                status_code=422,
                detail=f"{item.name}: carving line {n}: {design.sku} ({design.name}) "
                "has no picture yet. Add its picture in CNC designs first",
            )
        new_rows.append(FurnitureCarving(item_id=item_id, sort=n - 1, **c.model_dump()))
    lab_service.check_carvings(item, new_rows, parts, designs)
    with uow:
        uow.session.query(FurnitureCarving).filter(
            FurnitureCarving.item_id == item_id
        ).delete()
        uow.session.add_all(new_rows)
        for d in designs.values():
            if d.category == "general":
                d.category = "furniture"
        uow.commit()
    uow.session.expire_all()
    return _solved(uow, item)


@router.put("/items/{item_id}/presets")
def set_presets(
    item_id: UUID,
    body: PresetsIn,
    user: User = Depends(get_lab_editor),
    uow: AbstractUnitOfWork = Depends(get_uow),
):
    """Small/Medium/Large overall sizes. Existing parts must still work at every size."""
    item = _item(uow, item_id)
    new = {
        k: getattr(body, k).model_dump() for k in ("S", "M", "L") if getattr(body, k)
    }
    with uow:
        item.size_presets = new or None
        _check_all(uow, item)  # raises -> rolled back
        uow.commit()
    return {"presets": lab_service.presets(item)}


@router.put("/items/{item_id}/size")
def set_size(
    item_id: UUID,
    body: SizeIn,
    user: User = Depends(get_lab_editor),
    uow: AbstractUnitOfWork = Depends(get_uow),
):
    """The piece's standard overall size (L x W x H, mm). Existing parts must still work."""
    item = _item(uow, item_id)
    with uow:
        item.dimensions_mm = {"l": body.l, "w": body.w, "h": body.h}
        _check_all(uow, item)  # raises -> rolled back
        uow.commit()
    return {"dimensions_mm": item.dimensions_mm}


@router.put("/items/{item_id}/furniture")
def set_furniture(
    item_id: UUID,
    body: FurnitureIn,
    user: User = Depends(get_lab_editor),
    uow: AbstractUnitOfWork = Depends(get_uow),
):
    item = _item(uow, item_id)
    with uow:
        item.is_furniture = body.is_furniture
        uow.commit()
    return {"id": str(item.id), "is_furniture": item.is_furniture}


# ------------------------------------------------------------------ open the viewer
@router.post("/links")
def make_link(
    body: LinkIn,
    request: Request,
    user: User = Depends(get_lab_user),
    uow: AbstractUnitOfWork = Depends(get_uow),
):
    """
    A 10-minute link that opens the viewer in a browser. The link is NOT a
    login: its token is scoped to the Lab and is exchanged for a Lab-only
    cookie, so it cannot call anything else.
    """
    if body.mode == "edit" and user.role not in LAB_EDIT_ROLES:
        raise HTTPException(
            status_code=403, detail="Only the owner or a manager can edit parts"
        )
    if body.mode == "edit":
        _item(uow, body.item_id)
    else:
        _furniture(uow, body.item_id)
    token = create_scoped_token(
        str(user.id),
        "lab-link",
        LINK_MINUTES,
        item=str(body.item_id),
        mode=body.mode,
        tv=user.token_version or 0,
    )
    path = f"/lab/open?t={token}"
    return {
        "path": path,
        "url": str(request.base_url).rstrip("/") + path,
        "expires_in": LINK_MINUTES * 60,
    }


@router.get("/open", include_in_schema=False)
def open_link(t: str, request: Request, uow: AbstractUnitOfWork = Depends(get_uow)):
    payload = verify_token(t)
    if payload.get("scope") != "lab-link" or not payload.get("item"):
        raise HTTPException(status_code=401, detail="This link is not valid")
    user = uow.users.get_by_id(payload["sub"])
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="This link is not valid")
    check_token_version(payload, user)
    mode = "edit" if payload.get("mode") == "edit" else "view"
    resp = RedirectResponse(f"/lab/{mode}/{payload['item']}", status_code=303)
    resp.set_cookie(
        LAB_COOKIE,
        create_scoped_token(
            str(user.id), "lab", SESSION_MINUTES, tv=user.token_version or 0
        ),
        max_age=SESSION_MINUTES * 60,
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
        path="/lab",
    )
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["Cache-Control"] = "no-store"
    return resp


_UUID = r"[0-9a-fA-F-]{36}"


def _page(name: str) -> FileResponse:
    return FileResponse(STATIC_DIR / name, media_type="text/html", headers=PAGE_HEADERS)


@router.get("/view/{item_id}", include_in_schema=False)
def view_page(item_id: str):
    if not re.fullmatch(_UUID, item_id):
        raise HTTPException(status_code=404, detail="Not found")
    return _page("view.html")


@router.get("/edit/{item_id}", include_in_schema=False)
def edit_page(item_id: str):
    if not re.fullmatch(_UUID, item_id):
        raise HTTPException(status_code=404, detail="Not found")
    return _page("edit.html")
