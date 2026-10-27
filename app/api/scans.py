"""
3D scan jobs: a phone captures photos or a video, the workshop GPU PC
reconstructs them later. The table is the queue (docs/design/scan-and-lab.md,
section 5).

Phone:  POST /scans -> POST /scans/{id}/presign -> upload straight to the store
        -> POST /scans/{id}/complete
GPU PC: GET /scans?status=uploaded -> POST claim -> GET download-urls ->
        POST progress (renews the lease) -> POST results/presign, upload ->
        POST finish (or fail)

Neither side holds cloud keys: the server only signs URLs and checks objects.
"""

import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Literal, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import and_, or_, update

from app.core.ratelimit import public_rate_limit
from app.db.deps import (
    RESTORATION_ROLES,
    check_role,
    get_current_user,
    get_uow,
)
from app.models.enums import UserRole
from app.models.restoration import RestorationFile, RestorationProject
from app.models.scan_job import ScanJob
from app.models.user import User
from app.repositories.base import AbstractUnitOfWork
from app.services import media_service
from app.services.asset_service import attach_private_asset
from app.services.object_store import S3Store, get_store, get_store_or_none

router = APIRouter(prefix="/scans", tags=["Scans"])

PROCESSORS = [UserRole.OWNER, UserRole.MANAGER]  # the GPU PC logs in as one of these
# Restoration scans are processed by the owner or an artisan account (never a manager).
WORKERS = PROCESSORS + [UserRole.ARTISAN]
LEASE = timedelta(minutes=30)
MAX_ATTEMPTS = 3
MIN_PHOTOS, MAX_PHOTOS = 8, 300
MB = 1024 * 1024
# source kind -> (extensions, max bytes per file, max files)
SOURCES = {
    "photos": ({".jpg", ".jpeg", ".png"}, 100 * MB, MAX_PHOTOS),
    "video": ({".mp4", ".mov"}, 1024 * MB, 1),
}
RESULT_KINDS = ["render", "glb", "stl", "obj", "ply", "nc"]
OPEN = ("created", "uploaded")


# ------------------------------------------------------------------ schemas
class ScanParams(BaseModel):
    real_width_mm: Optional[float] = Field(default=None, gt=0, le=5000)
    quality: Literal["fast", "normal", "high"] = "normal"
    gcode: bool = False


class ScanCreate(BaseModel):
    item_id: Optional[UUID] = None
    restoration_id: Optional[UUID] = None  # a temple restoration instead of a product
    source_kind: Literal["photos", "video"]
    params: ScanParams = ScanParams()


class FileSpec(BaseModel):
    filename: str = Field(max_length=200)
    content_type: str = Field(max_length=100)
    size: int


class PresignFiles(BaseModel):
    files: List[FileSpec] = Field(min_length=1, max_length=MAX_PHOTOS)


class Complete(BaseModel):
    keys: List[str] = Field(min_length=1, max_length=MAX_PHOTOS)


class Claim(BaseModel):
    worker: str = Field(min_length=1, max_length=60)  # e.g. "workshop-pc"


class Progress(Claim):
    percent: int = Field(ge=0, le=100)
    note: Optional[str] = Field(default=None, max_length=200)


class ResultPresign(Claim):
    kind: Literal["render", "glb", "stl", "obj", "ply", "nc"]
    filename: str = Field(max_length=200)
    content_type: str = Field(max_length=100)
    size: int


class ResultAsset(BaseModel):
    key: str = Field(max_length=300)
    kind: Literal["render", "glb", "stl", "obj", "ply", "nc"]
    visibility: Literal["public", "staff"] = "staff"
    content_type: str = Field(max_length=100)
    alt: Optional[str] = Field(default=None, max_length=200)


class Finish(Claim):
    item_id: Optional[UUID] = None
    assets: List[ResultAsset] = Field(min_length=1, max_length=20)


class Fail(Claim):
    error: str = Field(min_length=1, max_length=500)


class ScanOut(BaseModel):
    id: UUID
    item_id: Optional[UUID]
    restoration_id: Optional[UUID] = None
    item_name: Optional[str]
    status: str
    source_kind: str
    files: int
    source_bytes: int
    params: dict
    progress: int
    note: Optional[str]
    error: Optional[str]
    attempts: int
    worker: Optional[str]
    lease_until: Optional[datetime]
    created_at: datetime
    finished_at: Optional[datetime]
    result_asset_ids: list


# ------------------------------------------------------------------ helpers
def _now() -> datetime:
    return datetime.now(timezone.utc)


def _out(job: ScanJob, uow: AbstractUnitOfWork) -> ScanOut:
    name = None
    if job.item_id:
        item = uow.items.get_by_id(job.item_id)
        name = item.name if item else None
    return ScanOut(
        id=job.id,
        item_id=job.item_id,
        restoration_id=job.restoration_id,
        item_name=name,
        status=job.status,
        source_kind=job.source_kind,
        files=len(job.source_keys or []),
        source_bytes=job.source_bytes,
        params=job.params,
        progress=job.progress,
        note=job.note,
        error=job.error,
        attempts=job.attempts,
        worker=job.claimed_by.split("|", 1)[1] if job.claimed_by else None,
        lease_until=job.lease_until,
        created_at=job.created_at,
        finished_at=job.finished_at,
        result_asset_ids=job.result_asset_ids or [],
    )


def _get(uow: AbstractUnitOfWork, scan_id: UUID) -> ScanJob:
    job = uow.session.get(ScanJob, scan_id)
    if not job:
        raise HTTPException(status_code=404, detail="Scan not found")
    return job


def _visible(job: ScanJob, user: User) -> None:
    """
    Capture is open to all staff, but you only see your own scans; owner/manager see
    all. A restoration scan is seen by the owner and every artisan, and by nobody else
    (a manager is told it does not exist).
    """
    if job.restoration_id:
        if user.role in RESTORATION_ROLES:
            return
    elif user.role in PROCESSORS or job.created_by == user.id:
        return
    raise HTTPException(status_code=404, detail="Scan not found")


def _may_process(job: ScanJob, user: User) -> None:
    """Who may run a job on the workshop PC: owner/manager, or for a restoration owner/artisan."""
    allowed = RESTORATION_ROLES if job.restoration_id else PROCESSORS
    if user.role not in allowed:
        raise HTTPException(status_code=404, detail="Scan not found")


def _claimed(job: ScanJob, user: User, worker: str) -> None:
    _may_process(job, user)
    if job.status != "processing" or job.claimed_by != f"{user.id}|{worker}":
        raise HTTPException(
            status_code=409, detail="This scan is not claimed by this worker"
        )


def _expire_stuck(uow: AbstractUnitOfWork) -> None:
    """A job whose lease lapsed after the last allowed attempt gives up for good."""
    uow.session.execute(
        update(ScanJob)
        .where(
            ScanJob.status == "processing",
            ScanJob.lease_until < _now(),
            ScanJob.attempts >= MAX_ATTEMPTS,
        )
        .values(
            status="failed",
            error="The workshop PC stopped responding; gave up after 3 attempts.",
            finished_at=_now(),
        )
    )
    uow.commit()


def _check_prefix(keys: List[str], prefix: str) -> None:
    for k in keys:
        if not k.startswith(prefix) or ".." in k:
            raise HTTPException(
                status_code=400, detail="Key does not belong to this scan"
            )


# ------------------------------------------------------------------ phone side
@router.post("", response_model=ScanOut, status_code=201)
def create_scan(
    req: ScanCreate,
    user: User = Depends(get_current_user),
    uow: AbstractUnitOfWork = Depends(get_uow),
):
    if req.item_id and req.restoration_id:
        raise HTTPException(
            status_code=422, detail="A scan is for a product or a restoration, not both"
        )
    if req.item_id and not uow.items.get_by_id(req.item_id):
        raise HTTPException(status_code=404, detail="Item not found")
    if req.restoration_id:
        if user.role not in RESTORATION_ROLES:
            raise HTTPException(
                status_code=403,
                detail="Restoration scans are for the owner and artisans",
            )
        if not uow.session.get(RestorationProject, req.restoration_id):
            raise HTTPException(status_code=404, detail="Restoration not found")
    job = ScanJob(
        item_id=req.item_id,
        restoration_id=req.restoration_id,
        created_by=user.id,
        source_kind=req.source_kind,
        params=req.params.model_dump(),
    )
    uow.session.add(job)
    uow.commit()
    uow.refresh(job)
    return _out(job, uow)


@router.post("/{scan_id}/presign", dependencies=[Depends(public_rate_limit("scan"))])
def presign_sources(
    scan_id: UUID,
    req: PresignFiles,
    user: User = Depends(get_current_user),
    uow: AbstractUnitOfWork = Depends(get_uow),
    store: S3Store = Depends(get_store),
):
    """Signed uploads for the capture files. The STORE enforces size and type."""
    job = _get(uow, scan_id)
    _visible(job, user)
    if job.status != "created":
        raise HTTPException(status_code=409, detail="This scan is already uploaded")
    exts, max_bytes, max_files = SOURCES[job.source_kind]
    if len(req.files) > max_files:
        raise HTTPException(
            status_code=422,
            detail=f"At most {max_files} file(s) for a {job.source_kind} scan",
        )
    out = []
    for f in req.files:
        ext = Path(f.filename or "").suffix.lower()
        if ext not in exts:
            raise HTTPException(
                status_code=415,
                detail=f"{job.source_kind} scans accept {', '.join(sorted(exts))} files",
            )
        if not media_service._CONTENT_TYPE.match(f.content_type or ""):
            raise HTTPException(status_code=400, detail="Invalid content type")
        if f.size < 1 or f.size > max_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"Each file must be 1 byte to {max_bytes // MB} MB",
            )
        key = f"scans/{job.id}/src/{uuid.uuid4().hex}{ext}"
        signed = store.presign_put(key, f.content_type, f.size)
        out.append(
            {
                "key": key,
                "url": signed["url"],
                "method": signed["method"],
                "headers": signed["headers"],
            }
        )
    return {"files": out, "expires_in": 600}


@router.post("/{scan_id}/complete", response_model=ScanOut)
def complete_upload(
    scan_id: UUID,
    req: Complete,
    user: User = Depends(get_current_user),
    uow: AbstractUnitOfWork = Depends(get_uow),
    store: S3Store = Depends(get_store),
):
    """Check every object really arrived, then queue the job for the workshop PC."""
    job = _get(uow, scan_id)
    _visible(job, user)
    if job.status == "uploaded" and sorted(job.source_keys) == sorted(set(req.keys)):
        return _out(job, uow)  # a retried call
    if job.status != "created":
        raise HTTPException(status_code=409, detail=f"Scan is already {job.status}")
    keys = sorted(set(req.keys))
    _check_prefix(keys, f"scans/{job.id}/src/")
    exts, max_bytes, max_files = SOURCES[job.source_kind]
    if len(keys) > max_files or any(Path(k).suffix.lower() not in exts for k in keys):
        raise HTTPException(status_code=422, detail="Files do not match this scan type")
    if job.source_kind == "photos" and len(keys) < MIN_PHOTOS:
        raise HTTPException(
            status_code=422,
            detail=f"Take at least {MIN_PHOTOS} photos, walking around the piece",
        )
    sizes = {k: store.head(k) for k in keys}
    missing = [k for k, s in sizes.items() if s is None]
    if missing:
        raise HTTPException(
            status_code=409,
            detail={"message": "Some files did not upload", "missing": missing},
        )
    if any(s > max_bytes for s in sizes.values()):
        raise HTTPException(status_code=413, detail="A file is larger than allowed")
    job.source_keys = keys
    job.source_bytes = sum(sizes.values())
    job.status = "uploaded"
    project = (
        uow.session.get(RestorationProject, job.restoration_id)
        if job.restoration_id
        else None
    )
    if project and project.status in ("planned", "capturing"):
        project.status = "processing"  # queued for the workshop PC
    uow.commit()
    uow.refresh(job)
    return _out(job, uow)


@router.post("/{scan_id}/cancel", response_model=ScanOut)
def cancel_scan(
    scan_id: UUID,
    user: User = Depends(get_current_user),
    uow: AbstractUnitOfWork = Depends(get_uow),
    store: Optional[S3Store] = Depends(get_store_or_none),
):
    job = _get(uow, scan_id)
    _visible(job, user)
    if job.status not in OPEN:
        raise HTTPException(
            status_code=409, detail=f"A {job.status} scan cannot be cancelled"
        )
    keys = list(job.source_keys or [])
    job.status = "cancelled"
    job.finished_at = _now()
    uow.commit()
    if store:
        for k in keys:  # best effort: the retention rule would remove them anyway
            try:
                store.delete(k)
            except Exception:
                pass
    uow.refresh(job)
    return _out(job, uow)


@router.get("", response_model=List[ScanOut])
def list_scans(
    status: Optional[str] = Query(default=None),
    restoration_id: Optional[UUID] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    user: User = Depends(get_current_user),
    uow: AbstractUnitOfWork = Depends(get_uow),
):
    """
    Own scans for staff; every product scan for owner/manager. `uploaded` lists oldest
    first (the queue). Restoration scans are listed only for the owner and artisans.
    """
    q = uow.session.query(ScanJob)
    if user.role not in RESTORATION_ROLES:
        q = q.filter(ScanJob.restoration_id.is_(None))
    if user.role not in PROCESSORS:
        q = q.filter(
            or_(ScanJob.created_by == user.id, ScanJob.restoration_id.isnot(None))
        )
    if restoration_id:
        q = q.filter(ScanJob.restoration_id == restoration_id)
    if status:
        q = q.filter(ScanJob.status == status)
    q = q.order_by(
        ScanJob.created_at.asc() if status == "uploaded" else ScanJob.created_at.desc()
    )
    return [_out(j, uow) for j in q.limit(limit).all()]


@router.get("/{scan_id}", response_model=ScanOut)
def get_scan(
    scan_id: UUID,
    user: User = Depends(get_current_user),
    uow: AbstractUnitOfWork = Depends(get_uow),
):
    job = _get(uow, scan_id)
    _visible(job, user)
    return _out(job, uow)


# ------------------------------------------------------------------ workshop PC side
@router.post("/{scan_id}/claim", response_model=ScanOut)
def claim_scan(
    scan_id: UUID,
    req: Claim,
    user: User = Depends(check_role(WORKERS)),
    uow: AbstractUnitOfWork = Depends(get_uow),
):
    """One statement decides the winner, so two PCs can never both take a job."""
    _may_process(_get(uow, scan_id), user)
    _expire_stuck(uow)
    now = _now()
    res = uow.session.execute(
        update(ScanJob)
        .where(
            ScanJob.id == scan_id,
            or_(
                ScanJob.status == "uploaded",
                and_(ScanJob.status == "processing", ScanJob.lease_until < now),
            ),
            ScanJob.attempts < MAX_ATTEMPTS,
        )
        .values(
            status="processing",
            claimed_by=f"{user.id}|{req.worker}",
            lease_until=now + LEASE,
            attempts=ScanJob.attempts + 1,
            progress=0,
            note="Claimed",
            error=None,
        )
    )
    uow.commit()
    if res.rowcount == 0:
        raise HTTPException(
            status_code=409, detail="This scan is not available to claim"
        )
    job = _get(uow, scan_id)
    uow.refresh(job)
    return _out(job, uow)


@router.post("/{scan_id}/progress", response_model=ScanOut)
def report_progress(
    scan_id: UUID,
    req: Progress,
    user: User = Depends(check_role(WORKERS)),
    uow: AbstractUnitOfWork = Depends(get_uow),
):
    job = _get(uow, scan_id)
    _claimed(job, user, req.worker)
    job.progress = req.percent
    job.note = req.note
    job.lease_until = _now() + LEASE
    uow.commit()
    uow.refresh(job)
    return _out(job, uow)


@router.get("/{scan_id}/download-urls")
def download_urls(
    scan_id: UUID,
    worker: str = Query(min_length=1, max_length=60),
    user: User = Depends(check_role(WORKERS)),
    uow: AbstractUnitOfWork = Depends(get_uow),
    store: S3Store = Depends(get_store),
):
    job = _get(uow, scan_id)
    _claimed(job, user, worker)
    return [{"key": k, "url": store.presign_get(k)} for k in job.source_keys]


@router.post("/{scan_id}/results/presign")
def presign_result(
    scan_id: UUID,
    req: ResultPresign,
    user: User = Depends(check_role(WORKERS)),
    uow: AbstractUnitOfWork = Depends(get_uow),
    store: S3Store = Depends(get_store),
):
    job = _get(uow, scan_id)
    _claimed(job, user, req.worker)
    ext = media_service.validate_private_upload(
        req.kind, req.filename, req.content_type, req.size
    )
    key = f"scans/{job.id}/out/{uuid.uuid4().hex}{ext}"
    signed = store.presign_put(key, req.content_type, req.size)
    return {
        "key": key,
        "url": signed["url"],
        "method": signed["method"],
        "headers": signed["headers"],
        "expires_in": 600,
    }


def _finish_restoration(
    uow: AbstractUnitOfWork, store: S3Store, job: ScanJob, req: Finish
) -> ScanOut:
    """Results of a restoration scan become private files of the project, never product assets."""
    keys = [a.key for a in req.assets]
    _check_prefix(keys, f"scans/{job.id}/out/")
    if len(set(keys)) != len(keys):
        raise HTTPException(status_code=422, detail="The same file is listed twice")
    if any(a.visibility == "public" for a in req.assets):
        raise HTTPException(
            status_code=400,
            detail="Restoration results are private and cannot be published",
        )
    sizes = {a.key: store.head(a.key) for a in req.assets}
    missing = [k for k, s in sizes.items() if s is None]
    if missing:
        raise HTTPException(
            status_code=409,
            detail={"message": "Some results did not upload", "missing": missing},
        )
    for a in req.assets:
        if sizes[a.key] > media_service.PRIVATE_KINDS[a.kind][1]:
            raise HTTPException(status_code=413, detail="A result file is too large")
    ids = []
    for a in req.assets:
        row = RestorationFile(
            restoration_id=job.restoration_id,
            scan_id=job.id,
            kind=a.kind,
            key=a.key,
            content_type=a.content_type,
            size_bytes=sizes[a.key],
            label=a.alt,
        )
        uow.session.add(row)
        uow.session.flush()
        ids.append(str(row.id))
    job.status = "done"
    job.progress = 100
    job.note = "Finished"
    job.result_asset_ids = ids
    job.finished_at = _now()
    project = uow.session.get(RestorationProject, job.restoration_id)
    if project and project.status == "processing":
        project.status = "review"  # results are in; artisans check them
    uow.commit()
    uow.refresh(job)
    return _out(job, uow)


@router.post("/{scan_id}/finish", response_model=ScanOut)
def finish_scan(
    scan_id: UUID,
    req: Finish,
    user: User = Depends(check_role(WORKERS)),
    uow: AbstractUnitOfWork = Depends(get_uow),
    store: S3Store = Depends(get_store),
):
    """Attach the results to the product. Staff-only unless a render/.glb is published."""
    job = _get(uow, scan_id)
    if job.status == "done" and job.claimed_by == f"{user.id}|{req.worker}":
        return _out(job, uow)  # a retried call
    _claimed(job, user, req.worker)
    if job.restoration_id:
        return _finish_restoration(uow, store, job, req)

    item_id = req.item_id or job.item_id
    if not item_id or not uow.items.get_by_id(item_id):
        raise HTTPException(
            status_code=422, detail="Choose the product these results belong to"
        )
    keys = [a.key for a in req.assets]
    _check_prefix(keys, f"scans/{job.id}/out/")
    if len(set(keys)) != len(keys):
        raise HTTPException(status_code=422, detail="The same file is listed twice")
    for a in req.assets:  # check everything first so a failure attaches nothing
        if a.visibility == "public" and not media_service.can_be_public(a.kind):
            raise HTTPException(
                status_code=400,
                detail=f"{a.kind} files are staff-only and cannot be published",
            )
    missing = [a.key for a in req.assets if store.head(a.key) is None]
    if missing:
        raise HTTPException(
            status_code=409,
            detail={"message": "Some results did not upload", "missing": missing},
        )

    ids = []
    for a in req.assets:
        asset = attach_private_asset(
            uow,
            store,
            item_id=item_id,
            key=a.key,
            kind=a.kind,
            visibility=a.visibility,
            content_type=a.content_type,
            alt=a.alt,
            scan_id=job.id,
        )
        ids.append(str(asset.id))
    job = _get(uow, scan_id)
    job.item_id = item_id
    job.status = "done"
    job.progress = 100
    job.note = "Finished"
    job.result_asset_ids = ids
    job.finished_at = _now()
    uow.commit()
    uow.refresh(job)
    return _out(job, uow)


@router.post("/{scan_id}/fail", response_model=ScanOut)
def fail_scan(
    scan_id: UUID,
    req: Fail,
    user: User = Depends(check_role(WORKERS)),
    uow: AbstractUnitOfWork = Depends(get_uow),
):
    job = _get(uow, scan_id)
    _claimed(job, user, req.worker)
    job.status = "failed"
    job.error = req.error
    job.finished_at = _now()
    uow.commit()
    uow.refresh(job)
    return _out(job, uow)
