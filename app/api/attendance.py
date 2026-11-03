from datetime import date
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from app.db.deps import check_role, get_current_user, get_uow
from app.models.attendance import Attendance
from app.models.enums import UserRole
from app.models.user import User
from app.repositories.base import AbstractUnitOfWork
from app.schemas.attendance import (
    AttendanceCreate,
    AttendanceResponse,
    AttendanceUpdate,
)
from app.schemas.plant_unit import GeoAttendanceIn, GeoPointIn
from app.services import workforce_service

router = APIRouter(prefix="/attendance", tags=["Workforce"])


@router.post("/", response_model=AttendanceResponse)
def mark_attendance(
    attendance: AttendanceCreate,
    current_user: User = Depends(
        check_role([UserRole.OWNER, UserRole.MANAGER, UserRole.SALES])
    ),
    uow: AbstractUnitOfWork = Depends(get_uow),
):
    """
    Mark attendance for a specific user.

    RBAC: Restricted to OWNER, MANAGER, and SALES roles.

    Args:
        attendance (AttendanceCreate): Attendance data.
        current_user (User): Authenticated user.
        uow (AbstractUnitOfWork): Unit of Work for database operations.

    Returns:
        AttendanceResponse: The created attendance record.
    """
    return workforce_service.mark_attendance(uow, attendance, current_user.id)


@router.post("/mark-present", response_model=AttendanceResponse)
def mark_present_at_location(
    body: GeoAttendanceIn,
    current_user: User = Depends(check_role([UserRole.OWNER, UserRole.MANAGER])),
    uow: AbstractUnitOfWork = Depends(get_uow),
):
    """
    Mark a person present from the manager's phone, with the phone's GPS position.

    RBAC: OWNER and MANAGER only (SALES keeps the older routes, not this one).

    The server picks the plant unit from the position and the wage from the person's
    own base_daily_wage; the request cannot name either. A position inside no unit is
    recorded as location_status="off_site" for the owner to review, not rejected.
    A position that is out of range or reported with accuracy_m above
    GPS_MAX_ACCURACY_M (100 m by default) is rejected with 422.
    """
    return workforce_service.mark_present_at_location(uow, body, current_user)


@router.post("/me/check-in", response_model=AttendanceResponse)
def check_in_me(
    body: GeoPointIn,
    current_user: User = Depends(get_current_user),
    uow: AbstractUnitOfWork = Depends(get_uow),
):
    """
    Check yourself in from your own phone, with its GPS position.

    Any signed-in person. The server picks the plant unit from the position. Away from
    every unit is recorded as location_status="off_site" (your manager sees it), not
    refused. One record a day; accuracy_m above GPS_MAX_ACCURACY_M gives 422.
    """
    return workforce_service.check_in_self(uow, current_user, body)


@router.post("/me/check-out", response_model=AttendanceResponse)
def check_out_me(
    body: GeoPointIn,
    current_user: User = Depends(get_current_user),
    uow: AbstractUnitOfWork = Depends(get_uow),
):
    """End your day: closes today's check-in and works out the hours and wage."""
    return workforce_service.check_out_self(uow, current_user, body)


@router.get("/me/today", response_model=Optional[AttendanceResponse])
def my_day(
    current_user: User = Depends(get_current_user),
    uow: AbstractUnitOfWork = Depends(get_uow),
):
    """Today's record for me, or null: tells the phone which button to show."""
    return workforce_service.todays_record(uow, current_user.id)


@router.post("/check-in", response_model=AttendanceResponse)
def check_in(
    user_id: UUID,
    current_user: User = Depends(
        check_role([UserRole.OWNER, UserRole.MANAGER, UserRole.SALES])
    ),
    uow: AbstractUnitOfWork = Depends(get_uow),
):
    """
    Mark a user as checked in.

    RBAC: Restricted to OWNER, MANAGER, and SALES roles.

    Args:
        user_id (UUID): ID of the user to check in.
        current_user (User): Authenticated user.
        uow (AbstractUnitOfWork): Unit of Work for database operations.

    Returns:
        AttendanceResponse: The created attendance record.
    """
    return workforce_service.check_in(uow, user_id, current_user.id)


@router.post("/check-out", response_model=AttendanceResponse)
def check_out(
    attendance_id: UUID,
    current_user: User = Depends(
        check_role([UserRole.OWNER, UserRole.MANAGER, UserRole.SALES])
    ),
    uow: AbstractUnitOfWork = Depends(get_uow),
):
    """
    Mark a user as checked out.

    RBAC: Restricted to OWNER, MANAGER, and SALES roles.

    Args:
        attendance_id (UUID): ID of the attendance record to check out.
        current_user (User): Authenticated user.
        uow (AbstractUnitOfWork): Unit of Work for database operations.

    Returns:
        AttendanceResponse: The updated attendance record.
    """
    return workforce_service.check_out(uow, attendance_id, current_user.id)


@router.get("/my", response_model=List[AttendanceResponse])
def get_my_attendance(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(get_current_user),
    uow: AbstractUnitOfWork = Depends(get_uow),
):
    """
    Retrieve attendance records for the currently authenticated user with pagination.

    Args:
        limit (int): Maximum number of records to return.
        offset (int): Number of records to skip.
        current_user (User): Authenticated user.
        uow (AbstractUnitOfWork): Unit of Work for database operations.

    Returns:
        List[AttendanceResponse]: List of attendance records for the user.
    """
    return (
        uow.attendance.session.query(Attendance)
        .filter(Attendance.user_id == current_user.id)
        .limit(limit)
        .offset(offset)
        .all()
    )


@router.get("/all", response_model=List[AttendanceResponse])
def get_all_attendance(
    user_id: Optional[UUID] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    location_status: Optional[str] = Query(None, pattern="^(on_site|off_site)$"),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(
        check_role([UserRole.OWNER, UserRole.MANAGER, UserRole.SALES])
    ),
    uow: AbstractUnitOfWork = Depends(get_uow),
):
    """
    Retrieve all attendance records with optional filters and pagination.
    location_status=off_site lists the GPS-marked rows outside every plant unit.

    RBAC: Restricted to OWNER, MANAGER, and SALES roles.

    Args:
        user_id (Optional[UUID]): Filter by user ID.
        start_date (Optional[date]): Filter by start date.
        end_date (Optional[date]): Filter by end date.
        location_status (Optional[str]): on_site or off_site.
        limit (int): Maximum records to return.
        offset (int): Records to skip.
        current_user (User): Authenticated user.
        uow (AbstractUnitOfWork): Unit of Work for database operations.

    Returns:
        List[AttendanceResponse]: List of attendance records matching the filters.
    """
    query = uow.attendance.session.query(Attendance)
    if user_id:
        query = query.filter(Attendance.user_id == user_id)
    if start_date:
        query = query.filter(Attendance.date >= start_date)
    if end_date:
        query = query.filter(Attendance.date <= end_date)
    if location_status:
        query = query.filter(Attendance.location_status == location_status)
    return query.limit(limit).offset(offset).all()


@router.patch("/{attendance_id}", response_model=AttendanceResponse)
def update_attendance_record(
    attendance_id: UUID,
    update_data: AttendanceUpdate,
    current_user: User = Depends(check_role([UserRole.OWNER, UserRole.MANAGER])),
    uow: AbstractUnitOfWork = Depends(get_uow),
):
    """
    Update an existing attendance record.

    RBAC: Restricted to OWNER and MANAGER roles.

    Args:
        attendance_id (UUID): ID of the attendance record to update.
        update_data (AttendanceUpdate): Updated data.
        current_user (User): Authenticated user.
        uow (AbstractUnitOfWork): Unit of Work for database operations.

    Returns:
        AttendanceResponse: The updated attendance record.

    Raises:
        HTTPException: If attendance record is not found.
    """
    db_attendance = workforce_service.update_attendance(uow, attendance_id, update_data)
    if not db_attendance:
        raise HTTPException(status_code=404, detail="Attendance record not found")
    return db_attendance
