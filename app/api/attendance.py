from typing import List, Optional
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from datetime import date

from app.db.deps import get_db, get_current_user, check_role
from app.models.user import User
from app.models.attendance import Attendance
from app.schemas.attendance import (
    AttendanceCreate,
    AttendanceResponse,
    AttendanceUpdate,
)
from app.services import workforce_service

router = APIRouter(prefix="/attendance", tags=["Workforce"])


@router.post("/", response_model=AttendanceResponse)
def record_attendance(
    attendance: AttendanceCreate,
    current_user: User = Depends(check_role(["owner", "manager", "sales"])),
    db: Session = Depends(get_db),
):
    return workforce_service.mark_attendance(db, attendance, current_user.id)


@router.get("/", response_model=List[AttendanceResponse])
def list_attendance(
    user_id: Optional[UUID] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    query = db.query(Attendance)

    # RBAC: Non-admin can only see their own attendance
    if current_user.role not in ["owner", "manager", "sales"]:
        user_id = current_user.id

    if user_id:
        query = query.filter(Attendance.user_id == user_id)
    if start_date:
        query = query.filter(Attendance.date >= start_date)
    if end_date:
        query = query.filter(Attendance.date <= end_date)

    return query.all()


@router.patch("/{attendance_id}", response_model=AttendanceResponse)
def update_attendance_record(
    attendance_id: UUID,
    update_data: AttendanceUpdate,
    current_user: User = Depends(check_role(["owner", "manager"])),
    db: Session = Depends(get_db),
):
    db_attendance = workforce_service.update_attendance(db, attendance_id, update_data)
    if not db_attendance:
        raise HTTPException(status_code=404, detail="Attendance record not found")
    return db_attendance
