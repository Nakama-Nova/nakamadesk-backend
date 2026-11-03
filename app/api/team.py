"""
Who reports to whom, and where each person's day stands.

The owner sets a person's manager with PATCH /auth/users/{id} (reports_to_id). Anyone
signed in may read the chart; a person sees the attendance of the people under them,
the owner of everyone.
"""

from datetime import date as Date
from datetime import datetime, timezone
from typing import List, Literal, Optional
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.db.deps import get_current_user, get_db
from app.models.attendance import Attendance
from app.models.user import User
from app.schemas.attendance import AttendanceResponse
from app.services import plant_unit_service, team_service

router = APIRouter(prefix="/team", tags=["Team"])


class Person(BaseModel):
    id: UUID
    full_name: Optional[str] = None
    username: str
    role: str
    reports_to_id: Optional[UUID] = None


class Node(Person):
    reports: List["Node"] = []


class Me(BaseModel):
    me: Person
    manager: Optional[Person] = None
    reports: List[Person] = Field(description="the people who report straight to me")


class TeamAttendanceRow(BaseModel):
    user_id: UUID
    full_name: Optional[str] = None
    role: str
    # not_in: no record that day. in: checked in, not out. out: day finished.
    # marked: a manager marked them present (no check-in time).
    state: Literal["not_in", "in", "out", "marked"]
    # true when the person's own check-in or check-out was away from every plant unit
    off_site: bool = False
    attendance: Optional[AttendanceResponse] = None


@router.get("/tree", response_model=List[Node])
def team_tree(_: User = Depends(get_current_user), db=Depends(get_db)):
    """Active staff as a tree, owner first."""
    return team_service.tree(db)


@router.get("/me", response_model=Me)
def my_team(user: User = Depends(get_current_user), db=Depends(get_db)):
    """Who I report to and who reports straight to me."""
    boss = db.get(User, user.reports_to_id) if user.reports_to_id else None
    direct = (
        db.query(User)
        .filter(User.reports_to_id == user.id, User.is_active.is_(True))
        .order_by(User.full_name, User.username)
        .all()
    )
    return {"me": user, "manager": boss, "reports": direct}


def _off_site(row: Attendance) -> bool:
    # a manager-marked row carries the manager's position, not the person's
    own = row.recorded_by == row.user_id
    return (own and row.location_status == plant_unit_service.OFF_SITE) or (
        row.check_out_location_status == plant_unit_service.OFF_SITE
    )


@router.get("/attendance", response_model=List[TeamAttendanceRow])
def team_attendance(
    on: Optional[Date] = None,
    user: User = Depends(get_current_user),
    db=Depends(get_db),
):
    """
    Where each person under me stands on a day (default today, UTC): not in, in, out.
    Empty when nobody reports to me. The owner sees every active person.
    """
    day = on or datetime.now(timezone.utc).date()
    ids = team_service.report_ids(db, user)
    if not ids:
        return []
    staff = (
        db.query(User)
        .filter(User.id.in_(ids))
        .order_by(User.full_name, User.username)
        .all()
    )
    latest = {}
    for row in (
        db.query(Attendance)
        .filter(Attendance.user_id.in_(ids), Attendance.date == day)
        .order_by(Attendance.created_at)
    ):
        latest[row.user_id] = row  # the newest row for each person wins
    out = []
    for s in staff:
        row = latest.get(s.id)
        if row is None:
            state = "not_in"
        elif row.check_in is None:
            state = "marked"
        else:
            state = "out" if row.check_out else "in"
        out.append(
            {
                "user_id": s.id,
                "full_name": s.full_name,
                "role": s.role,
                "state": state,
                "off_site": bool(row and _off_site(row)),
                "attendance": row,
            }
        )
    return out
