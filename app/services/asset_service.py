"""Attach an object that is already in the private store to a catalogue item."""

from pathlib import Path
from typing import Optional
from uuid import UUID

from fastapi import HTTPException

from app.models.product_asset import ProductAsset
from app.repositories.base import AbstractUnitOfWork
from app.services import media_service
from app.services.object_store import S3Store

PUBLIC_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".glb": "model/gltf-binary",
}


def attach_private_asset(
    uow: AbstractUnitOfWork,
    store: S3Store,
    *,
    item_id: UUID,
    key: str,
    kind: str,
    visibility: str,
    content_type: str,
    alt: Optional[str] = None,
    sort: int = 0,
    scan_id: Optional[UUID] = None,
) -> ProductAsset:
    """
    The caller has already checked that the item exists and that `key` is one it
    may attach. Staff-only files stay in the private store. A public file is
    copied to public storage after the usual content checks, and the private
    copy is deleted, so the private bucket never serves the website.
    """
    if kind not in media_service.PRIVATE_KINDS:
        raise HTTPException(status_code=400, detail=f"Unknown file kind '{kind}'")
    if visibility == "public" and not media_service.can_be_public(kind):
        raise HTTPException(
            status_code=400,
            detail=f"{kind} files are staff-only and cannot be published",
        )

    max_bytes = media_service.PRIVATE_KINDS[kind][1]
    size = store.head(key)
    if size is None:
        raise HTTPException(
            status_code=409, detail="Upload not found. Upload the file first"
        )
    if size > max_bytes:
        store.delete(key)
        raise HTTPException(status_code=413, detail="File is too large")

    storage, stored_key = "s3", key
    if visibility == "public":
        ext = Path(key).suffix.lower()
        content_type = PUBLIC_TYPES.get(ext, "")
        cap = media_service.PUBLISHABLE_MAX[kind]
        data = store.get_bytes(key, cap)
        if len(data) > cap:
            raise HTTPException(
                status_code=413,
                detail=f"Too large to publish (max {cap // media_service.MB} MB). Keep it staff-only",
            )
        _, checked_ext = media_service.validate(content_type, data)
        stored_key, storage = media_service.save_public(
            item_id, checked_ext, data, store
        )

    asset = ProductAsset(
        item_id=item_id,
        kind=kind,
        storage_key=stored_key,
        content_type=content_type,
        bytes=size,
        sort=sort,
        alt=alt,
        visibility=visibility,
        storage=storage,
        scan_id=scan_id,
    )
    try:
        with uow:
            uow.session.add(asset)
            uow.commit()
    except Exception:
        if storage in media_service.MEDIA_STORAGES:
            media_service.delete(stored_key, storage, store)
        raise
    if storage in media_service.MEDIA_STORAGES:
        store.delete(key)  # the private copy is no longer needed
    uow.refresh(asset)
    return asset
