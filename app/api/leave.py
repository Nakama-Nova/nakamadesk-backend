"""Leave: types, requests, balances and approval. See app/services/leave_service.py."""

from datetime import date
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db.deps import get_current_user, get_db
from app.iam import service as iam
from app.iam.service import require
from app.models.hr import LeaveType
from app.models.user import User
from app.schemas.hr import (
    LeaveBalance,
    LeaveDecision,
    LeaveReject,
    LeaveRequestCreate,
    LeaveRequestResponse,
    LeaveTypeCreate,
    LeaveTypeResponse,
    LeaveTypeUpdate,
)
from app.services import leave_service

router = APIRouter(prefix="/leave", tags=["Leave"])


@router.get("/types", response_model=List[LeaveTypeResponse])
def list_types(
    include_inactive: bool = Query(False),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """The kinds of leave (casual, sick, unpaid...). Anyone signed in."""
    query = db.query(LeaveType)
    if not (include_inactive and iam.allowed(db, current_user, "leave:type:manage")):
        query = query.filter(LeaveType.active.is_(True))
    return query.order_by(LeaveType.name).all()


@router.post("/types", response_model=LeaveTypeResponse, status_code=201)
def add_type(
    data: LeaveTypeCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require("leave:type:manage")),
):
    if db.query(LeaveType).filter(LeaveType.name == data.name).first():
        raise HTTPException(status_code=409, detail="That leave type exists")
    row = LeaveType(**data.model_dump())
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@router.put("/types/{type_id}", response_model=LeaveTypeResponse)
def change_type(
    type_id: UUID,
    data: LeaveTypeUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require("leave:type:manage")),
):
    row = leave_service.get_type(db, type_id)
    changes = data.model_dump(exclude_unset=True)
    if "name" in changes and changes["name"] is None:
        raise HTTPException(status_code=422, detail="The name cannot be empty")
    for key in ("paid", "active"):
        if key in changes and changes[key] is None:
            del changes[key]
    for key, value in changes.items():
        setattr(row, key, value)
    db.commit()
    db.refresh(row)
    return row


@router.post("/requests", response_model=LeaveRequestResponse, status_code=201)
def ask_for_leave(
    data: LeaveRequestCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require("leave:request:create")),
):
    """Ask for leave for yourself. 422 on an overlap or when a limited type runs out."""
    row = leave_service.create_request(db, db.get(User, current_user.id), data)
    return leave_service.request_out(db, row)


@router.get("/requests", response_model=List[LeaveRequestResponse])
def list_requests(
    status: Optional[str] = Query(
        None, pattern="^(pending|approved|rejected|cancelled)$"
    ),
    user_id: Optional[UUID] = Query(None),
    from_: Optional[date] = Query(None, alias="from"),
    to: Optional[date] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Yours; with leave:request:read_all, everyone's (or one person's with user_id)."""
    if iam.allowed(db, current_user, "leave:request:read_all"):
        ids = [user_id] if user_id else None
    else:
        ids = [current_user.id]
    return leave_service.list_requests(db, ids, status, from_, to)


@router.post("/requests/{request_id}/approve", response_model=LeaveRequestResponse)
def approve(
    request_id: UUID,
    body: Optional[LeaveDecision] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require("leave:request:approve")),
):
    """Approve: the days go into attendance as leave. You cannot decide your own."""
    row = leave_service.approve(
        db, db.get(User, current_user.id), request_id, body.note if body else None
    )
    return leave_service.request_out(db, row)


@router.post("/requests/{request_id}/reject", response_model=LeaveRequestResponse)
def reject(
    request_id: UUID,
    body: LeaveReject,
    db: Session = Depends(get_db),
    current_user: User = Depends(require("leave:request:approve")),
):
    row = leave_service.reject(db, db.get(User, current_user.id), request_id, body.note)
    return leave_service.request_out(db, row)


@router.post("/requests/{request_id}/cancel", response_model=LeaveRequestResponse)
def cancel(
    request_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """The person who asked, while it is pending or approved but not yet started."""
    row = leave_service.cancel(db, db.get(User, current_user.id), request_id)
    return leave_service.request_out(db, row)


@router.get("/balances/me", response_model=List[LeaveBalance])
def my_balances(
    on: Optional[date] = Query(None, description="any day in the leave year wanted"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return leave_service.balances(db, current_user.id, on)


@router.get("/balances", response_model=List[LeaveBalance])
def balances(
    user_id: Optional[UUID] = Query(None),
    on: Optional[date] = Query(None, description="any day in the leave year wanted"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Someone's balances for the leave year (April to March). Others' need leave:balance:read_all."""
    target = user_id or current_user.id
    if target != current_user.id and not iam.allowed(
        db, current_user, "leave:balance:read_all"
    ):
        raise HTTPException(
            status_code=403,
            detail="Operation not permitted. Missing permission: leave:balance:read_all",
        )
    if not db.get(User, target):
        raise HTTPException(status_code=404, detail="Staff member not found")
    return leave_service.balances(db, target, on)
