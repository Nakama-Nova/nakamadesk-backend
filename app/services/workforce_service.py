from datetime import datetime, timezone
from decimal import Decimal
from typing import List, Optional
from uuid import UUID

from app.models.attendance import Attendance
from app.models.daily_wage import DailyWage
from app.models.enums import UserRole
from app.models.user import User
from app.repositories.base import AbstractUnitOfWork
from app.schemas.attendance import AttendanceCreate, AttendanceUpdate
from app.schemas.plant_unit import GeoAttendanceIn, GeoPointIn
from app.schemas.wage import WagePaymentRequest
from app.services import plant_unit_service


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


def check_in(
    uow: AbstractUnitOfWork,
    user_id: UUID,
    recorder_id: UUID,
    location: Optional[plant_unit_service.Location] = None,
) -> Attendance:
    """
    Record the start of a workday for a user.

    One record a day: a second check-in after a check-out (or after a manager has marked
    the person) would count the day's wage twice, so it is refused.

    Args:
        uow (AbstractUnitOfWork): Unit of Work.
        user_id (UUID): ID of the user checking in.
        recorder_id (UUID): ID of the user performing the recording.
        location (Location): The checked GPS fix to store, when there is one.

    Returns:
        Attendance: The newly created attendance record.

    Raises:
        HTTPException: If the user already has a record today, or is not found.
    """
    from fastapi import HTTPException

    now = datetime.now(timezone.utc)
    today = now.date()

    with uow:
        # the lock makes two quick taps on the phone one check-in, not two
        user = (
            uow.session.query(User).filter(User.id == user_id).with_for_update().first()
        )
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        existing = (
            uow.attendance.session.query(Attendance)
            .filter(Attendance.user_id == user_id, Attendance.date == today)
            .first()
        )
        if existing:
            raise HTTPException(
                status_code=400,
                detail=(
                    "User already checked in today"
                    if existing.check_in and not existing.check_out
                    else "Attendance is already recorded for today"
                ),
            )

        db_attendance = Attendance(
            user_id=user_id,
            date=today,
            status="absent",  # Initially absent until check-out
            check_in=now,
            daily_wage=user.base_daily_wage or Decimal("0"),
            recorded_by=recorder_id,
            **(location.as_columns() if location else {}),
        )
        uow.attendance.add(db_attendance)
        uow.commit()
    uow.refresh(db_attendance)
    return db_attendance


def check_out(
    uow: AbstractUnitOfWork,
    attendance_id: UUID,
    recorder_id: UUID,
    location: Optional[plant_unit_service.Location] = None,
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
        if (
            db_attendance.check_in is None
        ):  # a row marked by a manager has no start time
            raise HTTPException(
                status_code=400,
                detail="This record has no check-in time; correct it with PATCH /attendance/{id}",
            )

        now = datetime.now(timezone.utc)
        db_attendance.check_out = now
        if location:
            for key, value in location.as_columns("check_out_").items():
                setattr(db_attendance, key, value)

        # Calculate hours
        # In SQLA, if columns are naive, we make them aware for comparison if needed
        ci = db_attendance.check_in
        if ci and not ci.tzinfo:
            ci = ci.replace(tzinfo=timezone.utc)

        delta = now - ci
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


def _locate(uow: AbstractUnitOfWork, point: GeoPointIn):
    from fastapi import HTTPException

    try:
        return plant_unit_service.locate(
            uow.session, point.latitude, point.longitude, point.accuracy_m
        )
    except plant_unit_service.GeoError as e:
        raise HTTPException(status_code=422, detail=str(e)) from None


def check_in_self(uow: AbstractUnitOfWork, user: User, point: GeoPointIn) -> Attendance:
    """
    A person checks themselves in from their own phone.

    The server checks the position and picks the plant unit (a client sends a position,
    never a unit). A position inside no unit is still recorded, as off_site, so the
    person's manager can see it; it is not refused, because the work is not always at
    the workshop (a temple, a delivery). The wage is the person's own base_daily_wage.
    """
    return check_in(uow, user.id, user.id, _locate(uow, point))


def check_out_self(
    uow: AbstractUnitOfWork, user: User, point: GeoPointIn
) -> Attendance:
    """A person ends their own day: closes today's open check-in, with the position."""
    from fastapi import HTTPException

    location = _locate(uow, point)
    today = datetime.now(timezone.utc).date()
    open_row = (
        uow.attendance.session.query(Attendance)
        .filter(
            Attendance.user_id == user.id,
            Attendance.date == today,
            Attendance.check_in.isnot(None),
            Attendance.check_out.is_(None),
        )
        .first()
    )
    if not open_row:
        raise HTTPException(status_code=400, detail="You have not checked in today")
    return check_out(uow, open_row.id, user.id, location)


def todays_record(uow: AbstractUnitOfWork, user_id: UUID) -> Optional[Attendance]:
    """The person's attendance row for today (UTC), if there is one."""
    return (
        uow.attendance.session.query(Attendance)
        .filter(
            Attendance.user_id == user_id,
            Attendance.date == datetime.now(timezone.utc).date(),
        )
        .order_by(Attendance.created_at.desc())
        .first()
    )


def mark_attendance(
    uow: AbstractUnitOfWork,
    attendance_data: AttendanceCreate,
    recorder_id: UUID,
    location: Optional[plant_unit_service.Location] = None,
) -> Attendance:
    """
    Explicitly record attendance for a specific date (used for bulk or sync).

    Calculates the linked wage immediately upon record creation.

    Args:
        uow (AbstractUnitOfWork): Unit of Work.
        attendance_data (AttendanceCreate): Details including user, date, and status.
        recorder_id (UUID): ID of the person recording.
        location (Location): Where the recorder's phone was, already checked and
            matched to a plant unit by the server (see mark_present_at_location).

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
            **attendance_data.model_dump(),
            **(location.as_columns() if location else {}),
            recorded_by=recorder_id,
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


def mark_present_at_location(
    uow: AbstractUnitOfWork, data: GeoAttendanceIn, recorder: User
) -> Attendance:
    """
    A manager marks one person present from the phone, with the phone's GPS position.

    The server checks the position, picks the plant unit and takes the wage from the
    person's own base_daily_wage; none of those come from the client. A position inside
    no unit is still recorded, as off_site, for the owner to review. The date is today
    on the server. Everything else (one record a day, idempotent client_id, the wage
    entry) is mark_attendance.
    """
    from fastapi import HTTPException

    if data.user_id == recorder.id and recorder.role != UserRole.OWNER.value:
        raise HTTPException(
            status_code=403, detail="You cannot mark your own attendance"
        )
    staff = uow.users.get_by_id(data.user_id)
    if not staff or not staff.is_active:
        raise HTTPException(status_code=404, detail="User not found")
    try:
        location = plant_unit_service.locate(
            uow.session, data.latitude, data.longitude, data.accuracy_m
        )
    except plant_unit_service.GeoError as e:
        raise HTTPException(status_code=422, detail=str(e)) from None

    create = AttendanceCreate(
        user_id=staff.id,
        date=datetime.now(timezone.utc).date(),
        status=data.status,
        daily_wage=staff.base_daily_wage or Decimal("0"),
        client_id=data.client_id,
    )
    return mark_attendance(uow, create, recorder.id, location)


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

        now = datetime.now(timezone.utc)
        for wage in wages:
            wage.payment_status = "paid"
            wage.transaction_reference = payment_data.transaction_reference
            # Convert to naive if needed by model default, but here we provide aware
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
