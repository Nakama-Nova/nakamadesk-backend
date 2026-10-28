"""Staff endpoints for product photos and 3D models."""

from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from app.api.uploads import asset_out
from app.core.config import settings
from app.db.deps import check_role, get_uow
from app.models.enums import UserRole
from app.models.product_asset import ProductAsset
from app.models.user import User
from app.repositories.base import AbstractUnitOfWork
from app.schemas.public import AssetResponse
from app.services import media_service
from app.services.object_store import S3Store

router = APIRouter(prefix="/items", tags=["Product assets"])

STAFF = [UserRole.OWNER, UserRole.MANAGER]


def _out(a: ProductAsset) -> AssetResponse:
    return asset_out(a)


@router.get("/{item_id}/assets", response_model=List[AssetResponse])
def list_assets(
    item_id: UUID,
    current_user: User = Depends(check_role(STAFF + [UserRole.ARTISAN])),
    uow: AbstractUnitOfWork = Depends(get_uow),
):
    """Owner and manager see every product's files; an artisan only a sculpture's (their own work)."""
    if current_user.role == UserRole.ARTISAN:
        item = uow.items.get_by_id(item_id)
        if not item or item.item_type != "sculpture":
            raise HTTPException(status_code=404, detail="Product not found")
    rows = (
        uow.session.query(ProductAsset)
        .filter(ProductAsset.item_id == item_id)
        .order_by(ProductAsset.sort, ProductAsset.created_at)
        .all()
    )
    return [_out(a) for a in rows]


@router.post("/{item_id}/assets", response_model=AssetResponse, status_code=201)
async def upload_asset(
    item_id: UUID,
    file: UploadFile = File(...),
    alt: Optional[str] = Form(None, max_length=200),
    sort: int = Form(0),
    current_user: User = Depends(check_role(STAFF)),
    uow: AbstractUnitOfWork = Depends(get_uow),
):
    """Upload a JPG/PNG/WebP photo (10 MB max) or a .glb model (25 MB max)."""
    if not uow.items.get_by_id(item_id):
        raise HTTPException(status_code=404, detail="Item not found")
    data = await file.read(media_service.max_read_size())
    kind, ext = media_service.validate(file.content_type or "", data)
    key = media_service.save(item_id, ext, data)
    asset = ProductAsset(
        item_id=item_id,
        kind=kind,
        storage_key=key,
        content_type=file.content_type,
        bytes=len(data),
        sort=sort,
        alt=alt,
        visibility="public",  # photos/models uploaded here are the public catalogue
        storage="local",
    )
    try:
        with uow:
            uow.session.add(asset)
            uow.commit()
    except Exception:
        media_service.delete(key)  # don't leave an orphan file behind
        raise
    uow.refresh(asset)
    return _out(asset)


@router.delete("/{item_id}/assets/{asset_id}", status_code=204)
def delete_asset(
    item_id: UUID,
    asset_id: UUID,
    current_user: User = Depends(check_role(STAFF)),
    uow: AbstractUnitOfWork = Depends(get_uow),
):
    asset = (
        uow.session.query(ProductAsset)
        .filter(ProductAsset.id == asset_id, ProductAsset.item_id == item_id)
        .first()
    )
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")
    key, storage = asset.storage_key, asset.storage
    with uow:
        uow.session.delete(asset)
        uow.commit()
    if storage == "s3":
        if settings.S3_BUCKET:
            S3Store().delete(key)
    else:
        media_service.delete(key)
