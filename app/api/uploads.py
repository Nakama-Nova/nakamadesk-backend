"""
Direct-to-store uploads for large and staff-only files.

Flow: POST /uploads/presign -> client PUT/POSTs the bytes to the store ->
POST /items/{id}/assets/register. The bytes never pass through this server.
Anything registered is staff-only unless an owner/manager explicitly publishes
a render or a finished-piece .glb; a published file is copied to the public
disk, so the private bucket never serves the website.
"""

from pathlib import Path
from typing import Literal, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from app.core.ratelimit import public_rate_limit
from app.db.deps import check_role, get_current_user, get_uow
from app.models.enums import UserRole
from app.models.product_asset import ProductAsset
from app.models.user import User
from app.repositories.base import AbstractUnitOfWork
from app.schemas.public import AssetResponse
from app.services import media_service
from app.services.asset_service import attach_private_asset
from app.services.object_store import S3Store, get_store, get_store_or_none

router = APIRouter(tags=["Uploads"])


class PresignRequest(BaseModel):
    item_id: UUID
    kind: str
    filename: str = Field(max_length=200)
    content_type: str = Field(max_length=100)
    size: int


class RegisterRequest(BaseModel):
    key: str = Field(max_length=300)
    kind: str
    visibility: Literal["public", "staff"] = "staff"
    content_type: str = Field(max_length=100)
    alt: Optional[str] = Field(default=None, max_length=200)
    sort: int = 0


@router.post(
    "/uploads/presign",
    dependencies=[Depends(public_rate_limit("presign"))],
)
def presign(
    req: PresignRequest,
    current_user: User = Depends(get_current_user),
    uow: AbstractUnitOfWork = Depends(get_uow),
    store: S3Store = Depends(get_store),
):
    """Signed upload for one file. The STORE enforces the size and content type."""
    ext = media_service.validate_private_upload(
        req.kind, req.filename, req.content_type, req.size
    )
    if not uow.items.get_by_id(req.item_id):
        raise HTTPException(status_code=404, detail="Item not found")
    key = media_service.private_key(req.item_id, req.kind, ext)
    max_bytes = media_service.PRIVATE_KINDS[req.kind][1]
    signed = store.presign_post(key, req.content_type, max_bytes)
    return {
        "key": key,
        "url": signed["url"],
        "fields": signed["fields"],
        "max_bytes": max_bytes,
        "expires_in": 600,
    }


@router.post(
    "/items/{item_id}/assets/register",
    response_model=AssetResponse,
    status_code=201,
)
def register_asset(
    item_id: UUID,
    req: RegisterRequest,
    current_user: User = Depends(check_role([UserRole.OWNER, UserRole.MANAGER])),
    uow: AbstractUnitOfWork = Depends(get_uow),
    store: S3Store = Depends(get_store),
):
    """Attach an uploaded object to a product (owner/manager only)."""
    if req.kind not in media_service.PRIVATE_KINDS:
        raise HTTPException(status_code=400, detail=f"Unknown file kind '{req.kind}'")
    if not uow.items.get_by_id(item_id):
        raise HTTPException(status_code=404, detail="Item not found")
    if not req.key.startswith(f"items/{item_id}/{req.kind}/") or ".." in req.key:
        raise HTTPException(status_code=400, detail="Key does not belong to this item")
    asset = attach_private_asset(
        uow,
        store,
        item_id=item_id,
        key=req.key,
        kind=req.kind,
        visibility=req.visibility,
        content_type=req.content_type,
        alt=req.alt,
        sort=req.sort,
    )
    return asset_out(asset)


@router.get("/items/{item_id}/assets/{asset_id}/download")
def download_asset(
    item_id: UUID,
    asset_id: UUID,
    current_user: User = Depends(get_current_user),
    uow: AbstractUnitOfWork = Depends(get_uow),
    store: Optional[S3Store] = Depends(get_store_or_none),
):
    """Staff download: a 15-minute signed link to the private store (or the public file)."""
    asset = (
        uow.session.query(ProductAsset)
        .filter(ProductAsset.id == asset_id, ProductAsset.item_id == item_id)
        .first()
    )
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")
    if asset.storage == "local":
        return RedirectResponse(media_service.url(asset.storage_key))
    name = f"{asset.kind}{Path(asset.storage_key).suffix}"
    if store is None:
        raise HTTPException(status_code=503, detail="File storage is not set up yet")
    return RedirectResponse(store.presign_get(asset.storage_key, filename=name))


def asset_out(a: ProductAsset) -> AssetResponse:
    return AssetResponse(
        id=a.id,
        item_id=a.item_id,
        kind=a.kind,
        url=(
            media_service.url(a.storage_key)
            if a.storage == "local"
            else f"/items/{a.item_id}/assets/{a.id}/download"
        ),
        alt=a.alt,
        sort=a.sort,
        bytes=a.bytes,
        visibility=a.visibility,
        storage=a.storage,
    )
