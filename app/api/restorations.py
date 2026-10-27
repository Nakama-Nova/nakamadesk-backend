"""
Temple restoration projects: which temple, which piece, its condition and where the
work stands, with the scans (photos/video) and the 3D results of each.

Visible only to the owner and artisans (RESTORATION_ROLES). Results are private
files of the project; they are not product assets, so the website can never show
them. Capture and the workshop PC use /scans with a restoration_id.
"""

import re
from datetime import datetime
from pathlib import Path
from typing import List, Literal, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func

from app.api.scans import ScanOut, _out
from app.db.deps import RESTORATION_ROLES, check_role, get_uow
from app.models.restoration import STATUSES, RestorationFile, RestorationProject
from app.models.scan_job import ScanJob
from app.models.user import User
from app.repositories.base import AbstractUnitOfWork
from app.services.object_store import S3Store, get_store

router = APIRouter(prefix="/restorations", tags=["Restoration"])

Status = Literal[
    "planned",
    "capturing",
    "processing",
    "review",
    "carving",
    "restored",
    "on_hold",
    "cancelled",
]
assert set(Status.__args__) == set(STATUSES)


class RestorationCreate(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    temple_name: str = Field(min_length=1, max_length=120)
    location: Optional[str] = Field(default=None, max_length=160)
    piece: Optional[str] = Field(default=None, max_length=160)
    condition_notes: Optional[str] = Field(default=None, max_length=2000)
    lead_artisan_id: Optional[UUID] = None


class RestorationUpdate(BaseModel):
    title: Optional[str] = Field(default=None, min_length=1, max_length=120)
    temple_name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    location: Optional[str] = Field(default=None, max_length=160)
    piece: Optional[str] = Field(default=None, max_length=160)
    condition_notes: Optional[str] = Field(default=None, max_length=2000)
    lead_artisan_id: Optional[UUID] = None
    status: Optional[Status] = None


class RestorationOut(BaseModel):
    id: UUID
    title: str
    temple_name: str
    location: Optional[str]
    piece: Optional[str]
    condition_notes: Optional[str]
    status: str
    lead_artisan_id: Optional[UUID]
    lead_artisan_name: Optional[str]
    scan_count: int
    file_count: int
    created_at: datetime
    updated_at: datetime


class FileOut(BaseModel):
    id: UUID
    scan_id: Optional[UUID]
    kind: str
    label: Optional[str]
    content_type: str
    size_bytes: int
    created_at: datetime


class RestorationDetail(RestorationOut):
    scans: List[ScanOut]
    files: List[FileOut]


class TeamMember(BaseModel):
    id: UUID
    name: str
    role: str


# ---------------------------------------------------------------- helpers
def _name(user: Optional[User]) -> Optional[str]:
    return (user.full_name or user.username) if user else None


def _project(uow: AbstractUnitOfWork, restoration_id: UUID) -> RestorationProject:
    project = uow.session.get(RestorationProject, restoration_id)
    if not project:
        raise HTTPException(status_code=404, detail="Restoration not found")
    return project


def _check_lead(uow: AbstractUnitOfWork, lead_id: Optional[UUID]) -> None:
    if lead_id is None:
        return
    user = uow.users.get_by_id(lead_id)
    if not user or not user.is_active or user.role not in RESTORATION_ROLES:
        raise HTTPException(
            status_code=422, detail="The lead must be an active artisan or the owner"
        )


def _out_project(uow: AbstractUnitOfWork, p: RestorationProject) -> dict:
    scans = (
        uow.session.query(func.count(ScanJob.id))
        .filter(ScanJob.restoration_id == p.id)
        .scalar()
    )
    files = (
        uow.session.query(func.count(RestorationFile.id))
        .filter(RestorationFile.restoration_id == p.id)
        .scalar()
    )
    lead = uow.users.get_by_id(p.lead_artisan_id) if p.lead_artisan_id else None
    return {
        "id": p.id,
        "title": p.title,
        "temple_name": p.temple_name,
        "location": p.location,
        "piece": p.piece,
        "condition_notes": p.condition_notes,
        "status": p.status,
        "lead_artisan_id": p.lead_artisan_id,
        "lead_artisan_name": _name(lead),
        "scan_count": scans,
        "file_count": files,
        "created_at": p.created_at,
        "updated_at": p.updated_at,
    }


# ---------------------------------------------------------------- routes
@router.get("/team", response_model=List[TeamMember])
def team(
    user: User = Depends(check_role(RESTORATION_ROLES)),
    uow: AbstractUnitOfWork = Depends(get_uow),
):
    """Who can lead a restoration: the owner and active artisans."""
    people = (
        uow.session.query(User)
        .filter(
            User.is_active.is_(True),
            User.role.in_([r.value for r in RESTORATION_ROLES]),
        )
        .order_by(User.full_name, User.username)
        .all()
    )
    return [TeamMember(id=u.id, name=_name(u), role=u.role) for u in people]


@router.post("", response_model=RestorationOut, status_code=201)
def create_restoration(
    req: RestorationCreate,
    user: User = Depends(check_role(RESTORATION_ROLES)),
    uow: AbstractUnitOfWork = Depends(get_uow),
):
    _check_lead(uow, req.lead_artisan_id)
    project = RestorationProject(**req.model_dump(), created_by=user.id)
    uow.session.add(project)
    uow.commit()
    uow.refresh(project)
    return _out_project(uow, project)


@router.get("", response_model=List[RestorationOut])
def list_restorations(
    status: Optional[Status] = Query(default=None),
    user: User = Depends(check_role(RESTORATION_ROLES)),
    uow: AbstractUnitOfWork = Depends(get_uow),
):
    query = uow.session.query(RestorationProject)
    if status:
        query = query.filter(RestorationProject.status == status)
    projects = query.order_by(RestorationProject.updated_at.desc()).all()
    return [_out_project(uow, p) for p in projects]


@router.get("/{restoration_id}", response_model=RestorationDetail)
def get_restoration(
    restoration_id: UUID,
    user: User = Depends(check_role(RESTORATION_ROLES)),
    uow: AbstractUnitOfWork = Depends(get_uow),
):
    project = _project(uow, restoration_id)
    scans = (
        uow.session.query(ScanJob)
        .filter(ScanJob.restoration_id == project.id)
        .order_by(ScanJob.created_at.desc())
        .all()
    )
    files = (
        uow.session.query(RestorationFile)
        .filter(RestorationFile.restoration_id == project.id)
        .order_by(RestorationFile.created_at.desc())
        .all()
    )
    return {
        **_out_project(uow, project),
        "scans": [_out(s, uow) for s in scans],
        "files": [FileOut.model_validate(f, from_attributes=True) for f in files],
    }


@router.patch("/{restoration_id}", response_model=RestorationOut)
def update_restoration(
    restoration_id: UUID,
    req: RestorationUpdate,
    user: User = Depends(check_role(RESTORATION_ROLES)),
    uow: AbstractUnitOfWork = Depends(get_uow),
):
    project = _project(uow, restoration_id)
    changes = req.model_dump(exclude_unset=True)
    for required in ("title", "temple_name", "status"):
        if required in changes and changes[required] is None:
            raise HTTPException(status_code=422, detail=f"{required} cannot be empty")
    if "lead_artisan_id" in changes:
        _check_lead(uow, changes["lead_artisan_id"])
    for key, value in changes.items():
        setattr(project, key, value)
    uow.commit()
    uow.refresh(project)
    return _out_project(uow, project)


@router.get("/{restoration_id}/files/{file_id}/download")
def download_file(
    restoration_id: UUID,
    file_id: UUID,
    user: User = Depends(check_role(RESTORATION_ROLES)),
    uow: AbstractUnitOfWork = Depends(get_uow),
    store: S3Store = Depends(get_store),
):
    """A short-lived signed link to one result (15 minutes), plus a sensible file name."""
    project = _project(uow, restoration_id)
    row = uow.session.get(RestorationFile, file_id)
    if not row or row.restoration_id != project.id:
        raise HTTPException(status_code=404, detail="File not found")
    stem = (
        re.sub(r"[^A-Za-z0-9]+", "-", project.title).strip("-").lower() or "restoration"
    )
    return {
        "url": store.presign_get(row.key),
        "filename": f"{stem}-{row.kind}{Path(row.key).suffix}",
        "size_bytes": row.size_bytes,
    }
