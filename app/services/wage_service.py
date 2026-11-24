"""
Pay types, wage history and what each pay type earns.

A person is paid per day (daily), per hour (hourly), per week or per month (weekly,
monthly). `user_wages` keeps every rate with the date it starts, and payroll uses the
rate in force on each day, so changing a rate never moves pay for days already worked.

  daily / hourly    The day's pay is fixed when the day is recorded (attendance wage x
                    status, or hourly rate x hours) and sits in daily_wages as before.
  weekly / monthly  Pay is worked out for every payable day since the last pay-out
                    (present, half day, paid leave, paid holidays, weekly offs); the
                    last pay-out's `through` date is what marks those days as paid.

`users.base_daily_wage` always stays the daily rate in force (weekly amount / 7,
monthly amount / days in the month, hourly amount x 8) so older screens keep working.
"""

import calendar
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import List, Optional
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.attendance import Attendance
from app.models.hr import Holiday, Shift, UserWage
from app.models.payroll import PayrollPayment
from app.models.user import User

PAY_TYPES = ("daily", "weekly", "monthly", "hourly")
SALARIED = ("weekly", "monthly")
CENT = Decimal("0.01")
HOURS_IN_A_DAY = Decimal("8")


def q(value) -> Decimal:
    return Decimal(value).quantize(CENT)


def days_in_month(day: date) -> int:
    return calendar.monthrange(day.year, day.month)[1]


def to_local(moment: datetime) -> datetime:
    """A stored check-in/out (UTC, maybe naive) as workshop wall-clock time."""
    if moment.tzinfo is not None:
        moment = moment.astimezone(timezone.utc).replace(tzinfo=None)
    return moment + timedelta(minutes=settings.BUSINESS_UTC_OFFSET_MINUTES)


def daily_equivalent(pay_type: str, amount, on: date) -> Decimal:
    """What a rate comes to for one day (the number base_daily_wage holds)."""
    amount = Decimal(amount or 0)
    if pay_type == "weekly":
        return q(amount / 7)
    if pay_type == "monthly":
        return q(amount / days_in_month(on))
    if pay_type == "hourly":
        return q(amount * HOURS_IN_A_DAY)
    return q(amount)


def amount_from_daily(pay_type: str, daily, on: date) -> Decimal:
    """The reverse: a daily figure (older screens send one) as the rate of a type."""
    daily = Decimal(daily or 0)
    if pay_type == "weekly":
        return q(daily * 7)
    if pay_type == "monthly":
        return q(daily * days_in_month(on))
    if pay_type == "hourly":
        return q(daily / HOURS_IN_A_DAY)
    return q(daily)


# ------------------------------------------------------------------ history


class Rate:
    __slots__ = ("effective_from", "pay_type", "amount")

    def __init__(self, effective_from: Optional[date], pay_type: str, amount):
        self.effective_from = effective_from
        self.pay_type = pay_type
        self.amount = Decimal(amount or 0)


def load_history(db: Session, user: User) -> List[Rate]:
    """Every rate of this person, oldest first. Empty for people never given one."""
    rows = (
        db.query(UserWage)
        .filter(UserWage.user_id == user.id)
        .order_by(UserWage.effective_from, UserWage.created_at)
        .all()
    )
    return [Rate(r.effective_from, r.pay_type, r.amount) for r in rows]


def current_rate(user: User) -> Rate:
    amount = user.pay_amount if user.pay_amount is not None else user.base_daily_wage
    return Rate(None, user.pay_type or "daily", amount or 0)


def rate_on(history: List[Rate], user: User, day: date) -> Rate:
    """The rate in force on a day. Before the first row, the first row; no rows, the user's own."""
    if not history:
        return current_rate(user)
    found = history[0]
    for rate in history:
        if rate.effective_from <= day:
            found = rate
        else:
            break
    return found


def last_paid_through(db: Session, user_id: UUID) -> Optional[date]:
    return (
        db.query(func.max(PayrollPayment.through))
        .filter(PayrollPayment.user_id == user_id)
        .scalar()
    )


def set_user_rate(user: User, pay_type: str, amount, on: date) -> None:
    user.pay_type = pay_type
    user.pay_amount = q(amount)
    user.base_daily_wage = daily_equivalent(pay_type, amount, on)


def baseline_date(user: User) -> date:
    created = user.created_at.date() if user.created_at else date.today()
    return min(d for d in (user.joining_date, created) if d)


def seed_baseline(db: Session, user: User, actor_id=None) -> None:
    """Give a person with no history a first row holding what they are paid today."""
    if db.query(UserWage.id).filter(UserWage.user_id == user.id).first():
        return
    rate = current_rate(user)
    db.add(
        UserWage(
            user_id=user.id,
            pay_type=rate.pay_type,
            amount=q(rate.amount),
            effective_from=baseline_date(user),
            note="Starting rate",
            created_by=actor_id,
        )
    )
    db.flush()


def refresh_current(db: Session, user: User, today: Optional[date] = None) -> None:
    """Move the user's own fields to the rate in force today (a dated raise arriving)."""
    today = today or date.today()
    hist = load_history(db, user)
    if not hist:
        return
    rate = rate_on(hist, user, today)
    daily = daily_equivalent(rate.pay_type, rate.amount, today)
    if (
        user.pay_type != rate.pay_type
        or user.pay_amount is None
        or Decimal(user.pay_amount) != q(rate.amount)
        or user.base_daily_wage is None
        or Decimal(user.base_daily_wage) != daily
    ):
        set_user_rate(user, rate.pay_type, rate.amount, today)


def add_wage(
    db: Session,
    actor: User,
    user: User,
    pay_type: str,
    amount,
    effective_from: date,
    note: Optional[str] = None,
) -> UserWage:
    """A new rate from a date. Not on or before the last pay-out's date."""
    paid_to = last_paid_through(db, user.id)
    if paid_to and effective_from <= paid_to:
        raise HTTPException(
            status_code=409,
            detail=f"Wages up to {paid_to.isoformat()} are already paid",
        )
    seed_baseline(db, user, actor.id)
    row = UserWage(
        user_id=user.id,
        pay_type=pay_type,
        amount=q(amount),
        effective_from=effective_from,
        note=note,
        created_by=actor.id,
    )
    db.add(row)
    db.flush()
    refresh_current(db, user)
    return row


def record_rate_edit(
    db: Session, actor: User, user: User, changes: dict, old_pay_type: str
) -> None:
    """
    The older edit (base_daily_wage; now also pay_type / pay_amount) still works and
    leaves a history row effective today, so nothing changes silently. Sending only
    base_daily_wage means "this daily figure": it is turned into the rate of the
    person's pay type. The caller has not yet set the user's own fields.
    """
    if not ({"base_daily_wage", "pay_type", "pay_amount"} & set(changes)):
        return
    today = date.today()
    new_type = changes.get("pay_type") or old_pay_type or "daily"
    if changes.get("pay_amount") is not None:
        amount = Decimal(changes["pay_amount"])
    elif changes.get("base_daily_wage") is not None:
        amount = amount_from_daily(new_type, changes["base_daily_wage"], today)
    else:  # only the type changed: keep the figure that was in force
        amount = Decimal(user.pay_amount if user.pay_amount is not None else 0)
    if new_type == (old_pay_type or "daily"):  # the staff form resends what it had
        if changes.get("pay_amount") is not None:
            same = Decimal(changes["pay_amount"]) == Decimal(user.pay_amount or -1)
        else:
            same = Decimal(changes.get("base_daily_wage") or 0) == Decimal(
                user.base_daily_wage or 0
            )
        if same:
            return
    seed_baseline(db, user, actor.id)  # before the new rate, from the old fields
    db.add(
        UserWage(
            user_id=user.id,
            pay_type=new_type,
            amount=q(amount),
            effective_from=today,
            note="Edited on the staff screen",
            created_by=actor.id,
        )
    )
    set_user_rate(user, new_type, amount, today)
    db.flush()


# ------------------------------------------------------------- day pay


def attendance_pay(
    db: Session,
    user_id: UUID,
    day: date,
    status: str,
    daily_wage,
    total_hours,
) -> Decimal:
    """
    What one recorded day earns for a daily or hourly person. A weekly or monthly
    person earns nothing here: their pay is worked out over the period at pay-out.
    """
    user = db.get(User, user_id)
    pay_type = rate_on(load_history(db, user), user, day).pay_type if user else "daily"
    status = (status or "").lower()
    daily_wage = Decimal(daily_wage or 0)
    if pay_type in SALARIED:
        return Decimal("0.00")
    if pay_type == "hourly":
        hours = total_hours
        if hours is None:
            hours = {"present": 8, "half-day": 4}.get(status, 0)
        return q((daily_wage / HOURS_IN_A_DAY) * Decimal(hours))
    if status == "present":
        return q(daily_wage)
    if status == "half-day":
        return q(daily_wage / 2)
    return Decimal("0.00")


def daily_rate_for(db: Session, user: User, day: date) -> Decimal:
    """The daily figure to stamp on an attendance row for a day (the rate in force then)."""
    hist = load_history(db, user)
    if not hist:
        return Decimal(user.base_daily_wage or 0)
    rate = rate_on(hist, user, day)
    return daily_equivalent(rate.pay_type, rate.amount, day)


# ----------------------------------------------------- salaried periods


def has_salaried(db: Session, user: User) -> bool:
    """Was this person ever on a weekly or monthly rate? (Cheap check before looping days.)"""
    if (user.pay_type or "daily") in SALARIED:
        return True
    return (
        db.query(UserWage.id)
        .filter(UserWage.user_id == user.id, UserWage.pay_type.in_(SALARIED))
        .first()
        is not None
    )


def weekly_offs(db: Session, user: User) -> set:
    if not user.shift_id:
        return set()
    shift = db.get(Shift, user.shift_id)
    return set(shift.weekly_off or []) if shift else set()


def salaried_period(
    db: Session, user: User, through: Optional[date] = None, cap_today: bool = True
) -> dict:
    """
    What a weekly/monthly person has earned since the last pay-out, up to `through`
    (default today): amount, payable days and unpaid days. Days when the person was
    on a daily or hourly rate are not counted here (their wages are in daily_wages).
    """
    empty = {
        "amount": Decimal("0.00"),
        "payable_days": Decimal(0),
        "unpaid_days": Decimal(0),
    }
    hist = load_history(db, user)
    end = through or date.today()
    if cap_today:
        end = min(end, date.today())
    if user.last_working_date:
        end = min(end, user.last_working_date)
    paid_to = last_paid_through(db, user.id)
    start = (paid_to + timedelta(days=1)) if paid_to else baseline_date(user)
    if user.joining_date:
        start = max(start, user.joining_date)
    if end < start:
        return empty

    rows = {}
    for row in (
        db.query(Attendance)
        .filter(
            Attendance.user_id == user.id,
            Attendance.date >= start,
            Attendance.date <= end,
        )
        .order_by(Attendance.created_at)
    ):
        have = rows.get(row.date)
        if have is None or have.status in ("absent", "leave"):
            rows[row.date] = row  # a day with a worked row beats a leave/absent one
    holidays = {
        h.date: h
        for h in db.query(Holiday).filter(Holiday.date >= start, Holiday.date <= end)
    }
    offs = weekly_offs(db, user)

    amount = Decimal(0)
    payable = Decimal(0)
    unpaid = Decimal(0)
    day = start
    while day <= end:
        rate = rate_on(hist, user, day)
        if rate.pay_type in SALARIED:
            per_day = rate.amount / (
                7 if rate.pay_type == "weekly" else days_in_month(day)
            )
            row = rows.get(day)
            rest = False  # a holiday or weekly off nobody worked: not "unpaid"
            if row is not None and row.status == "present":
                factor = Decimal(1)
            elif row is not None and row.status == "half-day":
                factor = Decimal("0.5")
            elif row is not None and row.status == "leave":
                if row.paid:
                    factor = Decimal("0.5") if row.leave_half_day else Decimal(1)
                else:
                    factor = Decimal(0)
            elif day in holidays:
                factor, rest = (Decimal(1) if holidays[day].paid else Decimal(0)), True
            elif day.weekday() in offs:
                factor, rest = Decimal(1), True
            else:
                factor = Decimal(0)
            amount += per_day * factor
            payable += factor
            if not rest:
                unpaid += Decimal(1) - factor
        day += timedelta(days=1)
    return {"amount": q(amount), "payable_days": payable, "unpaid_days": unpaid}
