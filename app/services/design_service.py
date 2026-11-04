"""A design's picture: validated, stored once, kept private, served by the API."""

import hashlib
import uuid
from typing import Optional

from fastapi import HTTPException
from fastapi.responses import Response

from app.models.design import Design
from app.repositories.base import AbstractUnitOfWork
from app.services import media_service
from app.services.object_store import S3Store


def store_image(
    uow: AbstractUnitOfWork,
    design: Design,
    data: bytes,
    content_type: str,
    store: Optional[S3Store],
) -> None:
    """
    Set the design's picture. The same file cannot be stored twice: if another
    design already has these exact bytes the request is refused and names it,
    so a design is reused instead of copied.
    """
    kind, ext = media_service.validate(content_type, data)
    if kind != "image":
        raise HTTPException(status_code=415, detail="Only JPG, PNG or WebP pictures")
    digest = hashlib.sha256(data).hexdigest()
    same = (
        uow.session.query(Design)
        .filter(Design.image_sha256 == digest, Design.id != design.id)
        .first()
    )
    if same:
        raise HTTPException(
            status_code=409,
            detail=f"This picture is already stored as {same.sku} ({same.name}). "
            "Use that design instead of adding it again",
        )
    old = design.image_key
    key = f"{media_service.PRIVATE_PREFIX}designs/{design.id}/{uuid.uuid4().hex}.{ext}"
    media_service.save_private(key, data, content_type, store)
    design.image_key, design.image_type, design.image_sha256 = key, content_type, digest
    try:
        uow.commit()
    except Exception:
        uow.rollback()
        media_service.delete_private(key, store)  # no orphan file behind a failed save
        raise
    if old:
        media_service.delete_private(old, store)


def image_response(design: Optional[Design], store: Optional[S3Store]) -> Response:
    """The picture's bytes, as a response that only the signed-in caller may cache."""
    if not design or not design.image_key:
        raise HTTPException(status_code=404, detail="No picture for this design")
    try:
        data = media_service.read_private(
            design.image_key, media_service.MAX_BYTES["image"], store
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="Picture not found") from None
    return Response(
        content=data,
        media_type=design.image_type or "image/png",
        headers={
            "Cache-Control": "private, max-age=300",
            "X-Content-Type-Options": "nosniff",
        },
    )
