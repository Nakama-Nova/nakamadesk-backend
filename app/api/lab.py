"""
The 3D Lab (staff only): parts of a furniture piece, solved for a size.

Everyone in the workshop (owner, manager, achari, worker) can VIEW. Only the
owner and managers can change part definitions. Viewing never changes data:
picking Small/Medium/Large or typing a custom size just recalculates.
"""

import re
from pathlib import Path
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy import func

from app.core.security import create_scoped_token, verify_token
from app.db.deps import (
    LAB_COOKIE,
    LAB_EDIT_ROLES,
    get_lab_editor,
    get_lab_user,
    get_uow,
)
from app.models.furniture_part import FurniturePart
from app.models.item import Item
from app.models.product_asset import ProductAsset
from app.models.raw_material import RawMaterial
from app.models.user import User
from app.repositories.base import AbstractUnitOfWork
from app.schemas.lab import (
    FurnitureIn,
    LabItem,
    LinkIn,
    PartOut,
    PartsIn,
    PresetsIn,
    SizeIn,
)
from app.services import lab_service
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
    return lab_service.solve(item, _parts(uow, item_id), size, l, w, h)


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
    if asset.storage == "local":
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
    for n, p in enumerate(body.parts):
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
        uow.session.query(FurniturePart).filter(
            FurniturePart.item_id == item_id
        ).delete()
        uow.session.add_all(new_parts)
        uow.commit()
    return lab_service.solve(item, _parts(uow, item_id))


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
        lab_service.check_definition(
            item, _parts(uow, item_id)
        )  # raises -> rolled back
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
        lab_service.check_definition(
            item, _parts(uow, item_id)
        )  # raises -> rolled back
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
        str(user.id), "lab-link", LINK_MINUTES, item=str(body.item_id), mode=body.mode
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
    mode = "edit" if payload.get("mode") == "edit" else "view"
    resp = RedirectResponse(f"/lab/{mode}/{payload['item']}", status_code=303)
    resp.set_cookie(
        LAB_COOKIE,
        create_scoped_token(str(user.id), "lab", SESSION_MINUTES),
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
