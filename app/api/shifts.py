"""Shifts and public holidays."""

from datetime import date
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db.deps import get_current_user, get_db
from app.iam.service import require
from app.models.hr import Holiday, Shift
from app.models.user import User
from app.schemas.hr import (
    HolidayCreate,
    HolidayResponse,
    ShiftCreate,
    ShiftResponse,
)
from app.services import hr_service

shifts = APIRouter(prefix="/shifts", tags=["Shifts"])
holidays = APIRouter(prefix="/holidays", tags=["Holidays"])


@shifts.get("", response_model=List[ShiftResponse])
def list_shifts(
    db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
):
    return db.query(Shift).order_by(Shift.name).all()


@shifts.post("", response_model=ShiftResponse, status_code=201)
def add_shift(
    data: ShiftCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require("shifts:shift:manage")),
):
    return hr_service.create_shift(db, data)


@shifts.put("/{shift_id}", response_model=ShiftResponse)
def change_shift(
    shift_id: UUID,
    data: ShiftCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require("shifts:shift:manage")),
):
    return hr_service.update_shift(db, shift_id, data)


@shifts.delete("/{shift_id}", status_code=204)
def remove_shift(
    shift_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(require("shifts:shift:manage")),
):
    """409 while anyone is on the shift."""
    hr_service.delete_shift(db, shift_id)


@holidays.get("", response_model=List[HolidayResponse])
def list_holidays(
    year: Optional[int] = Query(None, ge=2000, le=2100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    query = db.query(Holiday)
    if year:
        query = query.filter(
            Holiday.date >= date(year, 1, 1), Holiday.date <= date(year, 12, 31)
        )
    return query.order_by(Holiday.date).all()


@holidays.post("", response_model=HolidayResponse, status_code=201)
def add_holiday(
    data: HolidayCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require("holidays:holiday:manage")),
):
    return hr_service.create_holiday(db, data)


@holidays.delete("/{holiday_id}", status_code=204)
def remove_holiday(
    holiday_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(require("holidays:holiday:manage")),
):
    hr_service.delete_holiday(db, holiday_id)
