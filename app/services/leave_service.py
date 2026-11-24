"""
Leave: types, requests, balances and approval.

A request counts only the days the person would have worked: weekly offs (from their
shift) and public holidays are skipped. Approving it writes each of those days into
attendance as status "leave" (with the leave type and whether it is paid), which is how
the attendance sheet and payroll see it. The leave year runs April to March.
"""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import List, Optional
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.attendance import Attendance
from app.models.hr import Holiday, LeaveRequest, LeaveType
from app.models.user import User
from app.schemas.hr import LeaveRequestCreate
from app.services import wage_service

ACTIVE = ("pending", "approved")


def leave_year(day: date):
    """The April-to-March year a day falls in, as (first day, last day)."""
    first = date(day.year if day.month >= 4 else day.year - 1, 4, 1)
    return first, date(first.year + 1, 3, 31)


def _fmt(n) -> str:
    return format(Decimal(n).normalize(), "f")


def working_days(db: Session, user: User, start: date, end: date) -> List[date]:
    """The days in a range the person would work: no weekly offs, no holidays."""
    offs = wage_service.weekly_offs(db, user)
    holidays = {
        h.date
        for h in db.query(Holiday.date).filter(
            Holiday.date >= start, Holiday.date <= end
        )
    }
    out, day = [], start
    while day <= end:
        if day.weekday() not in offs and day not in holidays:
            out.append(day)
        day += timedelta(days=1)
    return out


def get_type(db: Session, type_id: UUID) -> LeaveType:
    row = db.get(LeaveType, type_id)
    if not row:
        raise HTTPException(status_code=404, detail="Leave type not found")
    return row


def _sum_days(
    db: Session,
    user_id: UUID,
    type_id: UUID,
    status: str,
    first: date,
    last: date,
    exclude: Optional[UUID] = None,
) -> Decimal:
    query = db.query(func.coalesce(func.sum(LeaveRequest.days), 0)).filter(
        LeaveRequest.user_id == user_id,
        LeaveRequest.leave_type_id == type_id,
        LeaveRequest.status == status,
        LeaveRequest.from_date >= first,
        LeaveRequest.from_date <= last,
    )
    if exclude:
        query = query.filter(LeaveRequest.id != exclude)
    return Decimal(query.scalar())


def balances(db: Session, user_id: UUID, on: Optional[date] = None) -> List[dict]:
    first, last = leave_year(on or date.today())
    out = []
    for t in (
        db.query(LeaveType)
        .filter(LeaveType.active.is_(True))
        .order_by(LeaveType.name)
        .all()
    ):
        used = _sum_days(db, user_id, t.id, "approved", first, last)
        pending = _sum_days(db, user_id, t.id, "pending", first, last)
        out.append(
            {
                "leave_type_id": t.id,
                "name": t.name,
                "yearly_days": t.yearly_days,
                "used": used,
                "pending": pending,
                "left": (
                    Decimal(t.yearly_days) - used - pending
                    if t.yearly_days is not None
                    else None
                ),
            }
        )
    return out


def _check_balance(
    db: Session, user: User, ltype: LeaveType, start: date, days: Decimal, exclude=None
) -> None:
    if ltype.yearly_days is None:
        return
    first, last = leave_year(start)
    taken = _sum_days(
        db, user.id, ltype.id, "approved", first, last, exclude
    ) + _sum_days(db, user.id, ltype.id, "pending", first, last, exclude)
    left = Decimal(ltype.yearly_days) - taken
    if days > left:
        raise HTTPException(
            status_code=422,
            detail=f"Only {_fmt(max(left, Decimal(0)))} days of {ltype.name} left",
        )


def create_request(db: Session, user: User, data: LeaveRequestCreate) -> LeaveRequest:
    ltype = get_type(db, data.leave_type_id)
    if not ltype.active:
        raise HTTPException(status_code=422, detail=f"{ltype.name} is not in use")
    clash = (
        db.query(LeaveRequest)
        .filter(
            LeaveRequest.user_id == user.id,
            LeaveRequest.status.in_(ACTIVE),
            LeaveRequest.from_date <= data.to_date,
            LeaveRequest.to_date >= data.from_date,
        )
        .first()
    )
    if clash:
        raise HTTPException(
            status_code=422,
            detail="You already have leave asked for or approved on those days",
        )
    days = Decimal(len(working_days(db, user, data.from_date, data.to_date)))
    if days == 0:
        raise HTTPException(
            status_code=422, detail="Those days are all weekly offs or holidays"
        )
    if data.half_day:
        days = Decimal("0.5")
    _check_balance(db, user, ltype, data.from_date, days)
    row = LeaveRequest(
        user_id=user.id,
        leave_type_id=ltype.id,
        from_date=data.from_date,
        to_date=data.to_date,
        half_day=data.half_day,
        days=days,
        reason=data.reason,
        status="pending",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _locked(db: Session, request_id: UUID) -> LeaveRequest:
    row = (
        db.query(LeaveRequest)
        .filter(LeaveRequest.id == request_id)
        .with_for_update()
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Leave request not found")
    return row


def _decide_checks(actor: User, row: LeaveRequest) -> None:
    if row.user_id == actor.id:
        raise HTTPException(
            status_code=403, detail="You cannot decide your own leave request"
        )
    if row.status != "pending":
        raise HTTPException(
            status_code=409, detail=f"This request is already {row.status}"
        )


def approve(
    db: Session, actor: User, request_id: UUID, note: Optional[str]
) -> LeaveRequest:
    row = _locked(db, request_id)
    _decide_checks(actor, row)
    user = db.get(User, row.user_id)
    ltype = get_type(db, row.leave_type_id)
    _check_balance(db, user, ltype, row.from_date, Decimal(row.days), exclude=row.id)
    for day in working_days(db, user, row.from_date, row.to_date):
        if (
            db.query(Attendance.id)
            .filter(Attendance.user_id == user.id, Attendance.date == day)
            .first()
        ):
            continue  # the person was marked or worked that day: leave does not replace it
        db.add(
            Attendance(
                user_id=user.id,
                date=day,
                status="leave",
                daily_wage=wage_service.daily_rate_for(db, user, day),
                recorded_by=actor.id,
                leave_type_id=ltype.id,
                leave_request_id=row.id,
                leave_half_day=bool(row.half_day),
                paid=bool(ltype.paid),
                payment_status="pending",
            )
        )
    row.status = "approved"
    row.decided_by = actor.id
    row.decided_at = datetime.now(timezone.utc)
    row.decision_note = note
    db.commit()
    db.refresh(row)
    return row


def reject(db: Session, actor: User, request_id: UUID, note: str) -> LeaveRequest:
    row = _locked(db, request_id)
    _decide_checks(actor, row)
    row.status = "rejected"
    row.decided_by = actor.id
    row.decided_at = datetime.now(timezone.utc)
    row.decision_note = note
    db.commit()
    db.refresh(row)
    return row


def cancel(db: Session, actor: User, request_id: UUID) -> LeaveRequest:
    row = _locked(db, request_id)
    if row.user_id != actor.id:
        raise HTTPException(
            status_code=403, detail="Only the person who asked can cancel it"
        )
    if row.status == "approved":
        if row.from_date <= date.today():
            raise HTTPException(
                status_code=409, detail="Leave that has started cannot be cancelled"
            )
        db.query(Attendance).filter(
            Attendance.leave_request_id == row.id, Attendance.status == "leave"
        ).delete(
            synchronize_session=False
        )  # the days are freed
    elif row.status != "pending":
        raise HTTPException(
            status_code=409, detail=f"This request is already {row.status}"
        )
    row.status = "cancelled"
    db.commit()
    db.refresh(row)
    return row


def list_requests(
    db: Session,
    user_ids: Optional[List[UUID]],
    status: Optional[str],
    start: Optional[date],
    end: Optional[date],
) -> List[dict]:
    query = (
        db.query(LeaveRequest, LeaveType, User)
        .join(LeaveType, LeaveType.id == LeaveRequest.leave_type_id)
        .join(User, User.id == LeaveRequest.user_id)
    )
    if user_ids is not None:
        query = query.filter(LeaveRequest.user_id.in_(user_ids))
    if status:
        query = query.filter(LeaveRequest.status == status)
    if start:
        query = query.filter(LeaveRequest.to_date >= start)
    if end:
        query = query.filter(LeaveRequest.from_date <= end)
    out = []
    for req, ltype, person in query.order_by(
        LeaveRequest.from_date.desc(), LeaveRequest.created_at.desc()
    ):
        out.append(
            {
                **{
                    c.name: getattr(req, c.name) for c in LeaveRequest.__table__.columns
                },
                "user_name": person.full_name or person.username,
                "leave_type_name": ltype.name,
                "paid": ltype.paid,
            }
        )
    return out


def request_out(db: Session, row: LeaveRequest) -> dict:
    ltype = db.get(LeaveType, row.leave_type_id)
    person = db.get(User, row.user_id)
    return {
        **{c.name: getattr(row, c.name) for c in LeaveRequest.__table__.columns},
        "user_name": (person.full_name or person.username) if person else None,
        "leave_type_name": ltype.name if ltype else None,
        "paid": ltype.paid if ltype else None,
    }
