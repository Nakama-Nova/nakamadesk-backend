"""A design's picture: validated, stored once, kept private, served by the API."""

import hashlib
import uuid
from typing import Optional, Tuple

from fastapi import HTTPException
from fastapi.responses import Response

from app.models.design import Design, DesignProduct
from app.models.item import Item
from app.models.product_asset import ProductAsset
from app.models.restoration import RestorationFile, RestorationProject
from app.models.scan_job import ScanJob
from app.models.user import User
from app.repositories.base import AbstractUnitOfWork
from app.services import media_service, sku_service
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


def _scan_picture(
    uow: AbstractUnitOfWork, job: ScanJob, store: Optional[S3Store]
) -> Tuple[bytes, str]:
    """The depth picture (kind 'render') the worker uploaded for a flat-carving scan."""
    if job.restoration_id:
        row = (
            uow.session.query(RestorationFile)
            .filter(RestorationFile.scan_id == job.id, RestorationFile.kind == "render")
            .order_by(RestorationFile.created_at)
            .first()
        )
        key, ctype, local = (row.key, row.content_type, False) if row else (None,) * 3
    else:
        asset = (
            uow.session.query(ProductAsset)
            .filter(ProductAsset.scan_id == job.id, ProductAsset.kind == "render")
            .order_by(ProductAsset.sort, ProductAsset.created_at)
            .first()
        )
        key = asset.storage_key if asset else None
        ctype = asset.content_type if asset else None
        local = bool(asset) and asset.storage == "local"
    if not key:
        raise HTTPException(
            status_code=409,
            detail="This scan has no depth picture (it was made before pictures were kept)",
        )
    try:
        if local:
            data = media_service._local_path(key).read_bytes()
        elif store is None:
            raise HTTPException(
                status_code=503, detail="File storage is not set up yet"
            )
        else:
            data = store.get_bytes(key, media_service.MAX_BYTES["image"])
    except (KeyError, FileNotFoundError):
        raise HTTPException(
            status_code=409, detail="The scan's depth picture is no longer stored"
        ) from None
    return data, ctype or "image/png"


def design_from_scan(
    uow: AbstractUnitOfWork,
    job: ScanJob,
    user: User,
    name: Optional[str],
    store: Optional[S3Store],
) -> Tuple[Design, bool]:
    """
    Make a furniture design from a finished flat-carving scan, on request: the
    scan's depth picture becomes the design's picture, its G-code and 3D files
    stay linked through scan_id, and the design is filed under "furniture".
    Returns (design, created). Asking twice for the same scan, or for a scan whose
    picture is already stored under another design, gives that design back
    instead of a copy.
    """
    if job.status != "done":
        raise HTTPException(status_code=409, detail="The scan is not finished yet")
    if (job.params or {}).get("shape") != "relief":
        raise HTTPException(
            status_code=422,
            detail="Only a flat-carving scan can become a design (it needs a depth picture)",
        )
    existing = uow.session.query(Design).filter(Design.scan_id == job.id).first()
    if existing:
        return existing, False
    data, ctype = _scan_picture(uow, job, store)
    twin = (
        uow.session.query(Design)
        .filter(Design.image_sha256 == hashlib.sha256(data).hexdigest())
        .first()
    )
    if twin:
        return (
            twin,
            False,
        )  # the same picture is already a design: use it, never copy it

    item = uow.session.get(Item, job.item_id) if job.item_id else None
    project = (
        uow.session.get(RestorationProject, job.restoration_id)
        if job.restoration_id
        else None
    )
    base = (
        name
        or (job.params or {}).get("label")
        or (
            f"{item.name} carving"
            if item
            else f"{project.title} carving" if project else "Scanned carving"
        )
    )
    width = (job.params or {}).get("real_width_mm")
    design = Design(
        sku=sku_service.next_design_sku(uow.session),
        name=base.strip()[:120],
        description=f"From flat-carving scan {str(job.id)[:8]}"
        + (f", {width:g} mm wide" if width else ", size not set"),
        category="furniture",
        scan_id=job.id,
        restoration_id=job.restoration_id,
        created_by=user.id,
    )
    uow.session.add(design)
    uow.session.flush()
    if item:
        uow.session.add(DesignProduct(design_id=design.id, item_id=item.id))
    store_image(uow, design, data, ctype, store)  # commits
    return design, True
