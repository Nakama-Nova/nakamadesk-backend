from sqlalchemy.orm import Session
from app.models.attendance import Attendance
from app.models.daily_wage import DailyWage
from app.schemas.attendance import AttendanceCreate, AttendanceUpdate
from app.schemas.wage import WagePaymentRequest
from uuid import UUID
from decimal import Decimal
from datetime import datetime, timezone
from typing import List, Optional


def calculate_wage(status: str, daily_wage: Decimal) -> Decimal:
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


def mark_attendance(
    db: Session, attendance_data: AttendanceCreate, recorder_id: UUID
) -> Attendance:
    # 0. Idempotency Check
    if attendance_data.client_id:
        existing = (
            db.query(Attendance)
            .filter(Attendance.client_id == attendance_data.client_id)
            .first()
        )
        if existing:
            return existing

    # Ensure no double attendance for the same day
    from fastapi import HTTPException

    existing_attendance = (
        db.query(Attendance)
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
    db_attendance = Attendance(**attendance_data.model_dump(), recorded_by=recorder_id)
    db.add(db_attendance)
    db.flush()  # Get ID

    # 2. Calculate and create wage entry
    total_amount = calculate_wage(db_attendance.status, db_attendance.daily_wage)
    db_wage = DailyWage(
        attendance_id=db_attendance.id,
        total_amount=total_amount,
        payment_status="pending",
    )
    db.add(db_wage)
    db.commit()
    db.refresh(db_attendance)
    return db_attendance


def get_pending_wages(db: Session, user_id: Optional[UUID] = None) -> List[DailyWage]:
    query = db.query(DailyWage).filter(DailyWage.payment_status == "pending")
    if user_id:
        query = query.join(Attendance).filter(Attendance.user_id == user_id)
    return query.all()


def pay_wages(db: Session, payment_data: WagePaymentRequest) -> List[DailyWage]:
    wages = (
        db.query(DailyWage)
        .filter(
            DailyWage.attendance_id.in_(payment_data.attendance_ids),
            DailyWage.payment_status == "pending",
        )
        .all()
    )

    now = datetime.now(timezone.utc)
    for wage in wages:
        wage.payment_status = "paid"
        wage.transaction_ref = payment_data.transaction_ref
        wage.paid_at = now

        # Also update the parent attendance record for consistency if needed
        wage.attendance.payment_status = "paid"

    db.commit()
    for wage in wages:
        db.refresh(wage)
    return wages


def update_attendance(
    db: Session, attendance_id: UUID, update_data: AttendanceUpdate
) -> Optional[Attendance]:
    """Updates attendance and re-calculates linked wage if necessary."""
    db_attendance = db.query(Attendance).filter(Attendance.id == attendance_id).first()
    if not db_attendance:
        return None

    for key, value in update_data.model_dump(exclude_unset=True).items():
        setattr(db_attendance, key, value)

    # If status or wage changed, re-calculate the linked wage entry
    if update_data.status or update_data.daily_wage:
        if db_attendance.wage_entry:
            db_attendance.wage_entry.total_amount = calculate_wage(
                db_attendance.status, db_attendance.daily_wage
            )

    db.commit()
    db.refresh(db_attendance)
    return db_attendance
