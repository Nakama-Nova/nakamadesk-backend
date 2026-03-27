from datetime import datetime, timezone
from decimal import Decimal
from typing import List, Optional
from uuid import UUID

from app.models.attendance import Attendance
from app.models.daily_wage import DailyWage
from app.repositories.base import AbstractUnitOfWork
from app.schemas.attendance import AttendanceCreate, AttendanceUpdate
from app.schemas.wage import WagePaymentRequest


def calculate_wage(status: str, daily_wage: Decimal) -> Decimal:
    """
    Determine the payable wage based on attendance status and base rate.

    Args:
        status (str): Attendance status ('present', 'half-day', 'absent').
        daily_wage (Decimal): The base daily rate for the user.

    Returns:
        Decimal: The calculated wage amount.
    """
    """
    Logic:
    - present -> full wage
    - half-day -> 50%
    - absent -> 0
    """
    status = status.lower()
    if status == "present":
        return daily_wage
    elif status == "half-day":
        return (daily_wage / Decimal("2")).quantize(Decimal("0.01"))
    else:
        return Decimal("0.00")


def check_in(uow: AbstractUnitOfWork, user_id: UUID, recorder_id: UUID) -> Attendance:
    """
    Record the start of a workday for a user.

    Args:
        uow (AbstractUnitOfWork): Unit of Work.
        user_id (UUID): ID of the user checking in.
        recorder_id (UUID): ID of the user performing the recording.

    Returns:
        Attendance: The newly created attendance record.

    Raises:
        HTTPException: If the user is already checked in or not found.
    """
    from fastapi import HTTPException

    # Check for existing open attendance today
    today = datetime.now(timezone.utc).date()
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    with uow:
        existing = (
            uow.attendance.session.query(Attendance)
            .filter(
                Attendance.user_id == user_id,
                Attendance.date == today,
                Attendance.check_out.is_(None),
            )
            .first()
        )
        if existing:
            raise HTTPException(status_code=400, detail="User already checked in today")

        user = uow.users.get_by_id(user_id)
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        db_attendance = Attendance(
            user_id=user_id,
            date=today,
            status="absent",  # Initially absent until check-out
            check_in=now,
            daily_wage=user.base_daily_wage,
            recorded_by=recorder_id,
        )
        uow.attendance.add(db_attendance)
        uow.commit()
    uow.refresh(db_attendance)
    return db_attendance


def check_out(
    uow: AbstractUnitOfWork, attendance_id: UUID, recorder_id: UUID
) -> Attendance:
    """
    Record the completion of a workday and calculate wages.

    Computes total hours, determines status (present/half-day/absent),
    and creates or updates a pending wage record.

    Args:
        uow (AbstractUnitOfWork): Unit of Work.
        attendance_id (UUID): ID of the attendance record to close.
        recorder_id (UUID): ID of the user recording the check-out.

    Returns:
        Attendance: The updated attendance record.

    Raises:
        HTTPException: If record not found or already checked out.
    """
    from fastapi import HTTPException

    with uow:
        db_attendance = uow.attendance.get_by_id(attendance_id)
        if not db_attendance:
            raise HTTPException(status_code=404, detail="Attendance record not found")
        if db_attendance.check_out:
            raise HTTPException(status_code=400, detail="User already checked out")

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        db_attendance.check_out = now

        # Calculate hours
        delta = db_attendance.check_out - db_attendance.check_in
        hours = Decimal(delta.total_seconds() / 3600).quantize(Decimal("0.01"))
        db_attendance.total_hours = hours

        # Determine status
        if hours >= 8:
            db_attendance.status = "present"
        elif hours >= 4:
            db_attendance.status = "half-day"
        else:
            db_attendance.status = "absent"

        # Calculate and create/update wage entry
        amount = calculate_wage(db_attendance.status, db_attendance.daily_wage)

        if db_attendance.wage_entry:
            db_attendance.wage_entry.amount = amount
        else:
            db_wage = DailyWage(
                attendance_id=db_attendance.id,
                amount=amount,
                payment_status="pending",
            )
            uow.wages.add(db_wage)

        uow.commit()
    uow.refresh(db_attendance)
    return db_attendance


def mark_attendance(
    uow: AbstractUnitOfWork, attendance_data: AttendanceCreate, recorder_id: UUID
) -> Attendance:
    """
    Explicitly record attendance for a specific date (used for bulk or sync).

    Calculates the linked wage immediately upon record creation.

    Args:
        uow (AbstractUnitOfWork): Unit of Work.
        attendance_data (AttendanceCreate): Details including user, date, and status.
        recorder_id (UUID): ID of the person recording.

    Returns:
        Attendance: The persisted attendance record.

    Raises:
        HTTPException: If attendance already exists for the user on that date.
    """
    # 0. Idempotency Check
    if attendance_data.client_id:
        existing = uow.attendance.get_by_client_id(attendance_data.client_id)
        if existing:
            return existing

    # Ensure no double attendance for the same day
    from fastapi import HTTPException

    with uow:
        existing_attendance = (
            uow.attendance.session.query(Attendance)
            .filter(
                Attendance.user_id == attendance_data.user_id,
                Attendance.date == attendance_data.date,
            )
            .first()
        )
        if existing_attendance:
            raise HTTPException(
                status_code=400,
                detail="Attendance already recorded for this user on this date",
            )

        # 1. Create attendance record
        db_attendance = Attendance(
            **attendance_data.model_dump(), recorded_by=recorder_id
        )
        uow.attendance.add(db_attendance)
        uow.flush()  # Get ID

        # 2. Calculate and create wage entry
        amount = calculate_wage(db_attendance.status, db_attendance.daily_wage)
        db_wage = DailyWage(
            attendance_id=db_attendance.id,
            amount=amount,
            payment_status="pending",
        )
        uow.wages.add(db_wage)
        uow.commit()
    uow.refresh(db_attendance)
    return db_attendance


def get_pending_wages(
    uow: AbstractUnitOfWork, user_id: Optional[UUID] = None
) -> List[DailyWage]:
    """
    Retrieve all wage records currently marked as 'pending'.

    Args:
        uow (AbstractUnitOfWork): Unit of Work.
        user_id (Optional[UUID]): Filter by a specific user if provided.

    Returns:
        List[DailyWage]: List of pending daily wage records.
    """
    query = uow.wages.session.query(DailyWage).filter(
        DailyWage.payment_status == "pending"
    )
    if user_id:
        query = query.join(Attendance).filter(Attendance.user_id == user_id)
    return query.all()


def pay_wages(
    uow: AbstractUnitOfWork, payment_data: WagePaymentRequest
) -> List[DailyWage]:
    """
    Process payments for a batch of wage records.

    Updates the status to 'paid' and attaches a transaction reference.

    Args:
        uow (AbstractUnitOfWork): Unit of Work.
        payment_data (WagePaymentRequest): List of attendance IDs and transaction ref.

    Returns:
        List[DailyWage]: The updated wage records.
    """
    with uow:
        wages = (
            uow.wages.session.query(DailyWage)
            .filter(
                DailyWage.attendance_id.in_(payment_data.attendance_ids),
                DailyWage.payment_status == "pending",
            )
            .all()
        )

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        for wage in wages:
            wage.payment_status = "paid"
            wage.transaction_ref = payment_data.transaction_ref
            wage.paid_at = now

            # Also update the parent attendance record for consistency if needed
            wage.attendance.payment_status = "paid"

        uow.commit()

    for wage in wages:
        uow.refresh(wage)
    return wages


def update_attendance(
    uow: AbstractUnitOfWork, attendance_id: UUID, update_data: AttendanceUpdate
) -> Optional[Attendance]:
    """
    Modify an existing attendance record and re-calculate wages if needed.

    Args:
        uow (AbstractUnitOfWork): Unit of Work.
        attendance_id (UUID): Unique ID of the record.
        update_data (AttendanceUpdate): New data to apply.

    Returns:
        Optional[Attendance]: The updated record, or None if not found.
    """
    with uow:
        db_attendance = uow.attendance.get_by_id(attendance_id)
        if not db_attendance:
            return None

        for key, value in update_data.model_dump(exclude_unset=True).items():
            setattr(db_attendance, key, value)

        # If status or wage changed, re-calculate the linked wage entry
        if update_data.status or update_data.daily_wage:
            if db_attendance.wage_entry:
                db_attendance.wage_entry.amount = calculate_wage(
                    db_attendance.status, db_attendance.daily_wage
                )

        uow.commit()
    uow.refresh(db_attendance)
    return db_attendance
