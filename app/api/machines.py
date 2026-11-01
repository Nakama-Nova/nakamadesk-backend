from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db.deps import check_role, get_db
from app.models.enums import UserRole
from app.models.user import User
from app.schemas.registers import (
    DowntimeOut,
    DowntimeStart,
    MachineIn,
    MachineOut,
    MachineUpdate,
    MaintenanceIn,
    MaintenanceOut,
)
from app.services import machine_service as service

router = APIRouter(prefix="/machines", tags=["Machines"])

STAFF = [UserRole.OWNER, UserRole.MANAGER]


@router.post("/", response_model=MachineOut, status_code=201)
def create_machine(
    body: MachineIn,
    _: User = Depends(check_role(STAFF)),
    db: Session = Depends(get_db),
):
    """Add a machine to the register. Owner and manager."""
    return service.create(db, body)


@router.get("/", response_model=List[MachineOut])
def list_machines(
    location_id: Optional[UUID] = None,
    status: Optional[str] = Query(None, pattern="^(active|down|retired)$"),
    limit: int = Query(100, ge=1, le=200),
    offset: int = Query(0, ge=0),
    _: User = Depends(check_role(STAFF)),
    db: Session = Depends(get_db),
):
    """Machines with what each is worth now and when it is next due for service."""
    return service.list_machines(db, location_id, status, limit, offset)


@router.get("/due", response_model=List[MachineOut])
def due_for_service(
    within_days: int = Query(14, ge=0, le=365),
    _: User = Depends(check_role(STAFF)),
    db: Session = Depends(get_db),
):
    """Machines overdue for service or due within `within_days`, soonest first."""
    return service.due_for_service(db, within_days)


@router.get("/{machine_id}", response_model=MachineOut)
def get_machine(
    machine_id: UUID,
    _: User = Depends(check_role(STAFF)),
    db: Session = Depends(get_db),
):
    return service.get(db, machine_id)


@router.put("/{machine_id}", response_model=MachineOut)
def update_machine(
    machine_id: UUID,
    body: MachineUpdate,
    _: User = Depends(check_role(STAFF)),
    db: Session = Depends(get_db),
):
    """Change a machine's details, or retire it (status "retired"). Never deleted."""
    return service.update(db, machine_id, body)


@router.post(
    "/{machine_id}/maintenance", response_model=MaintenanceOut, status_code=201
)
def add_maintenance(
    machine_id: UUID,
    body: MaintenanceIn,
    current_user: User = Depends(check_role(STAFF)),
    db: Session = Depends(get_db),
):
    """Record a service or repair that has been done, and what it cost."""
    return service.add_maintenance(db, machine_id, body, current_user.id)


@router.get("/{machine_id}/maintenance", response_model=List[MaintenanceOut])
def list_maintenance(
    machine_id: UUID,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    _: User = Depends(check_role(STAFF)),
    db: Session = Depends(get_db),
):
    return service.list_maintenance(db, machine_id, limit, offset)


@router.post("/{machine_id}/downtime", response_model=DowntimeOut, status_code=201)
def start_downtime(
    machine_id: UUID,
    body: DowntimeStart,
    current_user: User = Depends(check_role(STAFF)),
    db: Session = Depends(get_db),
):
    """The machine stopped working. Marks it down until downtime is ended."""
    return service.start_downtime(db, machine_id, body, current_user.id)


@router.post("/{machine_id}/downtime/end", response_model=DowntimeOut)
def end_downtime(
    machine_id: UUID,
    _: User = Depends(check_role(STAFF)),
    db: Session = Depends(get_db),
):
    """The machine is working again."""
    return service.end_downtime(db, machine_id)


@router.get("/{machine_id}/downtime", response_model=List[DowntimeOut])
def list_downtime(
    machine_id: UUID,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    _: User = Depends(check_role(STAFF)),
    db: Session = Depends(get_db),
):
    return service.list_downtime(db, machine_id, limit, offset)
