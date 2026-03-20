from sqlalchemy.orm import Session
from app.models.attendance import Attendance
from app.models.daily_wage import DailyWage
from app.models.user import User
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


def mark_attendance(db: Session, attendance_data: AttendanceCreate, recorder_id: UUID) -> Attendance:
    # 1. Create attendance record
    db_attendance = Attendance(
        **attendance_data.model_dump(),
        recorded_by=recorder_id
    )
    db.add(db_attendance)
    db.flush() # Get ID
    
    # 2. Calculate and create wage entry
    total_amount = calculate_wage(db_attendance.status, db_attendance.daily_wage)
    db_wage = DailyWage(
        attendance_id=db_attendance.id,
        total_amount=total_amount,
        payment_status="pending"
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
    wages = db.query(DailyWage).filter(
        DailyWage.attendance_id.in_(payment_data.attendance_ids),
        DailyWage.payment_status == "pending"
    ).all()
    
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
