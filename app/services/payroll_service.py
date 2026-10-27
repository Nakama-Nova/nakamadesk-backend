"""
Pay for the people who work here: daily wages (from attendance), extra earnings
(per-piece jobs, overtime hours, bonuses) and advances, settled in one pay-out.

All money is Decimal rounded to 2 places. A pay-out is the only thing that marks
wages as paid, so the books and the "paid" flags cannot drift apart.
"""

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import List, Optional
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.attendance import Attendance
from app.models.daily_wage import DailyWage
from app.models.payroll import PayrollPayment, WorkEntry, WorkerAdvance
from app.models.user import User
from app.schemas.money import AdvanceCreate, PayRequest, WorkEntryCreate

CENT = Decimal("0.01")
MAX_AMOUNT = Decimal("9999999999.99")  # what Numeric(12, 2) can hold


def q(value) -> Decimal:
    return Decimal(value).quantize(CENT)


def _user(db: Session, user_id: UUID, lock: bool = False) -> User:
    query = db.query(User).filter(User.id == user_id)
    user = (query.with_for_update() if lock else query).first()
    if not user:
        raise HTTPException(status_code=404, detail="Staff member not found")
    return user


def pending_daily(db: Session, user_id: UUID, through: Optional[date] = None):
    query = (
        db.query(func.coalesce(func.sum(DailyWage.amount), 0))
        .join(Attendance, DailyWage.attendance_id == Attendance.id)
        .filter(Attendance.user_id == user_id, DailyWage.payment_status == "pending")
    )
    if through:
        query = query.filter(Attendance.date <= through)
    return q(query.scalar())


def pending_extras(db: Session, user_id: UUID, through: Optional[date] = None):
    query = db.query(func.coalesce(func.sum(WorkEntry.amount), 0)).filter(
        WorkEntry.user_id == user_id, WorkEntry.status == "pending"
    )
    if through:
        query = query.filter(WorkEntry.work_date <= through)
    return q(query.scalar())


def advance_balance(db: Session, user_id: UUID) -> Decimal:
    total = (
        db.query(func.coalesce(func.sum(WorkerAdvance.amount), 0))
        .filter(WorkerAdvance.user_id == user_id)
        .scalar()
    )
    return q(total)


def owed_summary(db: Session, user: User) -> dict:
    daily = pending_daily(db, user.id)
    extras = pending_extras(db, user.id)
    return {
        "user_id": user.id,
        "full_name": user.full_name,
        "username": user.username,
        "role": user.role,
        "pending_daily": daily,
        "pending_extras": extras,
        "owed_gross": daily + extras,
        "advance_balance": advance_balance(db, user.id),
    }


def everyone_owed(db: Session) -> List[dict]:
    users = (
        db.query(User)
        .filter(User.is_active.is_(True))
        .order_by(User.full_name, User.username)
        .all()
    )
    return [owed_summary(db, u) for u in users]


def my_payroll(db: Session, user: User) -> dict:
    entries = (
        db.query(WorkEntry)
        .filter(WorkEntry.user_id == user.id, WorkEntry.status == "pending")
        .order_by(WorkEntry.work_date.desc())
        .all()
    )
    return {
        **owed_summary(db, user),
        "pending_entries": entries,
        "recent_payments": list_payments(db, user.id, limit=10),
    }


def create_work_entry(db: Session, actor: User, data: WorkEntryCreate) -> WorkEntry:
    user = _user(db, data.user_id)
    quantity, rate = data.quantity, data.rate
    if data.kind == "bonus":
        quantity = Decimal("1")  # a bonus is one lump sum, the rate IS the amount
    if rate is None and data.kind == "overtime":
        rate = user.overtime_rate_per_hour
        if not rate:
            raise HTTPException(
                status_code=400,
                detail="No overtime rate set for this person. Enter the rate per hour.",
            )
    if rate is None or rate <= 0:
        raise HTTPException(status_code=400, detail="Enter a rate above zero")
    amount = q(quantity * rate)
    if amount <= 0 or amount > MAX_AMOUNT:
        raise HTTPException(status_code=400, detail="That amount is not valid")
    entry = WorkEntry(
        user_id=user.id,
        work_date=data.work_date,
        kind=data.kind,
        description=data.description.strip(),
        quantity=quantity,
        rate=q(rate),
        amount=amount,
        item_id=data.item_id,
        created_by=actor.id,
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry


def delete_work_entry(db: Session, entry_id: UUID) -> None:
    entry = (
        db.query(WorkEntry).filter(WorkEntry.id == entry_id).with_for_update().first()
    )
    if not entry:
        raise HTTPException(status_code=404, detail="Entry not found")
    if entry.status != "pending":
        raise HTTPException(status_code=409, detail="This entry has already been paid")
    db.delete(entry)
    db.commit()


def list_work_entries(
    db: Session, user_id: Optional[UUID], status: Optional[str]
) -> List[WorkEntry]:
    query = db.query(WorkEntry)
    if user_id:
        query = query.filter(WorkEntry.user_id == user_id)
    if status:
        query = query.filter(WorkEntry.status == status)
    return query.order_by(WorkEntry.work_date.desc(), WorkEntry.created_at.desc()).all()


def add_advance(db: Session, actor: User, data: AdvanceCreate) -> WorkerAdvance:
    user = _user(db, data.user_id, lock=True)  # serialises with pay-outs
    amount = q(data.amount)
    if data.kind == "recovery":
        balance = advance_balance(db, user.id)
        if amount > balance:
            raise HTTPException(
                status_code=400,
                detail=f"They only owe Rs {balance}; that is more than the advance outstanding",
            )
        amount = -amount
    row = WorkerAdvance(
        user_id=user.id,
        given_on=data.given_on,
        amount=amount,
        kind=data.kind,
        notes=data.notes,
        created_by=actor.id,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def advance_ledger(db: Session, user_id: UUID) -> dict:
    entries = (
        db.query(WorkerAdvance)
        .filter(WorkerAdvance.user_id == user_id)
        .order_by(WorkerAdvance.given_on.desc(), WorkerAdvance.created_at.desc())
        .all()
    )
    return {"balance": advance_balance(db, user_id), "entries": entries}


def pay(db: Session, actor: User, req: PayRequest) -> PayrollPayment:
    """
    Settle everything one person has earned up to `through`, taking back part of an
    advance if asked. Locks the person's row so two clicks cannot pay twice.
    """
    user = _user(db, req.user_id, lock=True)
    wages = (
        db.query(DailyWage)
        .join(Attendance, DailyWage.attendance_id == Attendance.id)
        .filter(
            Attendance.user_id == user.id,
            DailyWage.payment_status == "pending",
            Attendance.date <= req.through,
        )
        .with_for_update(of=DailyWage)
        .all()
    )
    entries = (
        db.query(WorkEntry)
        .filter(
            WorkEntry.user_id == user.id,
            WorkEntry.status == "pending",
            WorkEntry.work_date <= req.through,
        )
        .with_for_update()
        .all()
    )
    gross_daily = q(sum((w.amount for w in wages), Decimal(0)))
    gross_extras = q(sum((e.amount for e in entries), Decimal(0)))
    gross = gross_daily + gross_extras
    if gross <= 0:
        raise HTTPException(status_code=400, detail="Nothing to pay up to that date")

    deduction = q(req.deduction)
    if deduction > gross:
        raise HTTPException(
            status_code=400, detail=f"Deduction is more than the pay due (Rs {gross})"
        )
    balance = advance_balance(db, user.id)
    if deduction > balance:
        raise HTTPException(
            status_code=400,
            detail=f"Deduction is more than the advance outstanding (Rs {balance})",
        )

    paid_on = req.paid_on or date.today()
    payment = PayrollPayment(
        user_id=user.id,
        paid_on=paid_on,
        through=req.through,
        gross_daily=gross_daily,
        gross_extras=gross_extras,
        deductions=deduction,
        net_paid=gross - deduction,
        method=req.method,
        reference=req.reference,
        notes=req.notes,
        paid_by=actor.id,
    )
    db.add(payment)
    db.flush()  # need its id

    now = datetime.now(timezone.utc)
    for wage in wages:
        wage.payment_status = "paid"
        wage.paid_at = now
        wage.payment_id = payment.id
        wage.transaction_reference = req.reference
        wage.attendance.payment_status = "paid"
    for entry in entries:
        entry.status = "paid"
        entry.payment_id = payment.id
    if deduction > 0:
        db.add(
            WorkerAdvance(
                user_id=user.id,
                given_on=paid_on,
                amount=-deduction,
                kind="recovery",
                notes="Taken from pay-out",
                payment_id=payment.id,
                created_by=actor.id,
            )
        )
    db.commit()
    db.refresh(payment)
    return payment


def list_payments(
    db: Session,
    user_id: Optional[UUID],
    start: Optional[date] = None,
    end: Optional[date] = None,
    limit: int = 200,
) -> List[PayrollPayment]:
    query = db.query(PayrollPayment)
    if user_id:
        query = query.filter(PayrollPayment.user_id == user_id)
    if start:
        query = query.filter(PayrollPayment.paid_on >= start)
    if end:
        query = query.filter(PayrollPayment.paid_on <= end)
    return (
        query.order_by(PayrollPayment.paid_on.desc(), PayrollPayment.created_at.desc())
        .limit(limit)
        .all()
    )
