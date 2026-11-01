"""
The machine register: what we own, what it is worth now, when it is next due for service,
what it has cost to keep running, and when it was down.

Worth now is straight-line depreciation by whole months, down to the salvage value:
    yearly write-off = (purchase_cost - salvage_value) / useful_life_years
    written off so far = yearly write-off / 12 x whole months since purchase_date
A machine with no useful life set is not written off (worth = what it cost).
"""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import List, Optional
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.machine import Machine, MachineDowntime, MachineMaintenance
from app.schemas.registers import (
    DowntimeStart,
    MachineIn,
    MachineOut,
    MachineUpdate,
    MaintenanceIn,
)
from app.services import stock_location_service

CENT = Decimal("0.01")


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _whole_months(start: date, end: date) -> int:
    months = (end.year - start.year) * 12 + (end.month - start.month)
    if end.day < start.day:
        months -= 1
    return max(0, months)


def depreciation(machine: Machine, today: Optional[date] = None):
    """(written off so far, worth now), or (None, None) when the cost is unknown."""
    if machine.purchase_cost is None:
        return None, None
    cost = Decimal(machine.purchase_cost)
    today = today or date.today()
    if not machine.useful_life_years or not machine.purchase_date:
        return Decimal("0.00"), cost.quantize(CENT)
    salvage = min(Decimal(machine.salvage_value or 0), cost)
    yearly = (cost - salvage) / machine.useful_life_years
    written = yearly / 12 * _whole_months(machine.purchase_date, today)
    written = min(written, cost - salvage).quantize(CENT)
    return written, (cost - written).quantize(CENT)


def service_due(session: Session, machine: Machine) -> Optional[date]:
    """Next service: the last scheduled one (or the purchase date) plus the interval."""
    if not machine.maintenance_every_days:
        return None
    last = (
        session.query(func.max(MachineMaintenance.performed_on))
        .filter(
            MachineMaintenance.machine_id == machine.id,
            MachineMaintenance.kind == "scheduled",
        )
        .scalar()
    )
    base = last or machine.purchase_date
    return base + timedelta(days=machine.maintenance_every_days) if base else None


def to_out(session: Session, machine: Machine) -> MachineOut:
    written, worth = depreciation(machine)
    spent = (
        session.query(func.coalesce(func.sum(MachineMaintenance.cost), 0))
        .filter(MachineMaintenance.machine_id == machine.id)
        .scalar()
    )
    due = service_due(session, machine)
    out = MachineOut.model_validate(machine)
    out.accumulated_depreciation = written
    out.book_value = worth
    out.maintenance_spent = Decimal(spent).quantize(CENT)
    out.service_due_on = due
    out.service_overdue = bool(
        due and machine.status != "retired" and due < date.today()
    )
    return out


def _get(session: Session, machine_id: UUID) -> Machine:
    machine = session.get(Machine, machine_id)
    if machine is None:
        raise HTTPException(status_code=404, detail="Machine not found")
    return machine


def _check_location(session: Session, location_id: Optional[UUID]) -> None:
    if location_id is not None:
        stock_location_service.get_location(session, location_id)


def _save(session: Session, machine: Machine) -> None:
    try:
        session.add(machine)
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(
            status_code=409, detail="A machine with that tag already exists"
        ) from None


def create(session: Session, data: MachineIn) -> MachineOut:
    _check_location(session, data.location_id)
    machine = Machine(**data.model_dump())
    _save(session, machine)
    session.refresh(machine)
    return to_out(session, machine)


def update(session: Session, machine_id: UUID, data: MachineUpdate) -> MachineOut:
    machine = _get(session, machine_id)
    _check_location(session, data.location_id)
    values = data.model_dump(exclude={"status"})
    for key, value in values.items():
        setattr(machine, key, value)
    if data.status is not None and data.status != machine.status:
        if machine.status == "down":
            raise HTTPException(
                status_code=409, detail="End the machine's downtime first"
            )
        machine.status = data.status
    _save(session, machine)
    session.refresh(machine)
    return to_out(session, machine)


def get(session: Session, machine_id: UUID) -> MachineOut:
    return to_out(session, _get(session, machine_id))


def list_machines(
    session: Session,
    location_id: Optional[UUID],
    status: Optional[str],
    limit: int,
    offset: int,
) -> List[MachineOut]:
    q = session.query(Machine)
    if location_id:
        q = q.filter(Machine.location_id == location_id)
    if status:
        q = q.filter(Machine.status == status)
    rows = q.order_by(Machine.tag).limit(limit).offset(offset).all()
    return [to_out(session, m) for m in rows]


def due_for_service(session: Session, within_days: int) -> List[MachineOut]:
    """Machines whose next service falls within `within_days` (overdue ones included)."""
    horizon = date.today() + timedelta(days=within_days)
    out = []
    for machine in session.query(Machine).filter(Machine.status != "retired"):
        due = service_due(session, machine)
        if due is not None and due <= horizon:
            out.append(to_out(session, machine))
    return sorted(out, key=lambda m: m.service_due_on)


# --------------------------------------------------------------------- maintenance
def add_maintenance(
    session: Session, machine_id: UUID, data: MaintenanceIn, user_id: UUID
) -> MachineMaintenance:
    machine = _get(session, machine_id)
    if machine.status == "retired":
        raise HTTPException(status_code=409, detail="This machine is retired")
    if data.performed_on > date.today():
        raise HTTPException(
            status_code=400, detail="Record maintenance after it has been done"
        )
    row = MachineMaintenance(
        machine_id=machine.id, created_by=user_id, **data.model_dump()
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def list_maintenance(
    session: Session, machine_id: UUID, limit: int, offset: int
) -> List[MachineMaintenance]:
    _get(session, machine_id)
    return (
        session.query(MachineMaintenance)
        .filter(MachineMaintenance.machine_id == machine_id)
        .order_by(
            MachineMaintenance.performed_on.desc(), MachineMaintenance.created_at.desc()
        )
        .limit(limit)
        .offset(offset)
        .all()
    )


# ----------------------------------------------------------------------- downtime
def _open_downtime(session: Session, machine_id: UUID) -> Optional[MachineDowntime]:
    return (
        session.query(MachineDowntime)
        .filter(
            MachineDowntime.machine_id == machine_id,
            MachineDowntime.ended_at.is_(None),
        )
        .first()
    )


def start_downtime(
    session: Session, machine_id: UUID, data: DowntimeStart, user_id: UUID
) -> MachineDowntime:
    machine = (
        session.query(Machine)
        .filter(Machine.id == machine_id)
        .with_for_update()
        .first()
    )
    if machine is None:
        raise HTTPException(status_code=404, detail="Machine not found")
    if machine.status == "retired":
        raise HTTPException(status_code=409, detail="This machine is retired")
    if _open_downtime(session, machine.id) is not None:
        raise HTTPException(status_code=409, detail="This machine is already down")
    row = MachineDowntime(machine_id=machine.id, reason=data.reason, created_by=user_id)
    machine.status = "down"
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def end_downtime(session: Session, machine_id: UUID) -> MachineDowntime:
    machine = (
        session.query(Machine)
        .filter(Machine.id == machine_id)
        .with_for_update()
        .first()
    )
    if machine is None:
        raise HTTPException(status_code=404, detail="Machine not found")
    row = _open_downtime(session, machine.id)
    if row is None:
        raise HTTPException(status_code=409, detail="This machine is not down")
    row.ended_at = _now()
    machine.status = "active"
    session.commit()
    session.refresh(row)
    return row


def list_downtime(
    session: Session, machine_id: UUID, limit: int, offset: int
) -> List[MachineDowntime]:
    _get(session, machine_id)
    return (
        session.query(MachineDowntime)
        .filter(MachineDowntime.machine_id == machine_id)
        .order_by(MachineDowntime.started_at.desc())
        .limit(limit)
        .offset(offset)
        .all()
    )
