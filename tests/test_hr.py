"""HR upgrade: pay types and wage history, leave, shifts, holidays, profile, documents, exit."""

import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest

from app.db.deps import get_current_user
from app.main import app
from app.models.attendance import Attendance
from app.models.daily_wage import DailyWage
from app.models.hr import Holiday, UserWage
from app.models.payroll import PayrollPayment, WorkerAdvance
from app.models.user import User
from app.services import wage_service


@pytest.fixture(autouse=True)
def _no_holidays_left_over(db):
    """Holidays are global; start and finish every test with none."""
    db.query(Holiday).delete()
    db.commit()
    yield
    db.query(Holiday).delete()
    db.commit()


def make(db, role="worker", **kw) -> User:
    tag = uuid.uuid4().hex[:8]
    user = User(
        username=f"{role}_{tag}",
        email=f"{role}_{tag}@example.com",
        password_hash="fakehash",
        role=role,
        is_active=True,
        **kw,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def act_as(user):
    app.dependency_overrides[get_current_user] = lambda: user


@pytest.fixture
def owner(client, db):
    user = make(db, "owner")
    act_as(user)
    return user


def mark(client, user, day, status, wage="0", **extra):
    resp = client.post(
        "/attendance/",
        json={
            "user_id": str(user.id),
            "date": str(day),
            "status": status,
            "daily_wage": wage,
            **extra,
        },
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def new_leave_type(client, name=None, paid=True, yearly_days=None):
    resp = client.post(
        "/leave/types",
        json={
            "name": name or f"Leave {uuid.uuid4().hex[:6]}",
            "paid": paid,
            "yearly_days": yearly_days,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def add_rate(client, user, pay_type, amount, effective_from, note=None):
    return client.post(
        f"/people/users/{user.id}/wages",
        json={
            "pay_type": pay_type,
            "amount": str(amount),
            "effective_from": str(effective_from),
            "note": note,
        },
    )


def summary_row(client, user):
    rows = client.get("/payroll/summary").json()
    return next(r for r in rows if r["user_id"] == str(user.id))


# March 2026: the 1st is a Sunday, 31 days, so a monthly rate of 31000 is 1000 a day.
MARCH = date(2026, 3, 1)


def d(n):
    return MARCH + timedelta(days=n - 1)  # d(1) = 1 March


# ------------------------------------------------------------ H2: wage history


def test_adding_a_rate_updates_the_user_and_keeps_history(client, db, owner):
    staff = make(db, base_daily_wage=Decimal("500"))
    resp = add_rate(client, staff, "monthly", "31000", date.today(), "Fixed salary")
    assert resp.status_code == 201
    db.refresh(staff)
    assert staff.pay_type == "monthly"
    assert staff.pay_amount == Decimal("31000.00")
    days = wage_service.days_in_month(date.today())
    assert staff.base_daily_wage == wage_service.q(Decimal(31000) / days)

    add_rate(client, staff, "hourly", "75", date.today())
    db.refresh(staff)
    assert staff.base_daily_wage == Decimal("600.00")  # hourly x 8
    add_rate(client, staff, "weekly", "3500", date.today())
    db.refresh(staff)
    assert staff.base_daily_wage == Decimal("500.00")  # weekly / 7

    rows = client.get(f"/people/users/{staff.id}/wages").json()
    assert rows[0]["pay_type"] == "weekly"
    # the starting rate was kept, so nothing changed silently
    assert {r["pay_type"] for r in rows} >= {"daily", "monthly", "hourly", "weekly"}
    starting = [r for r in rows if r["note"] == "Starting rate"][0]
    assert Decimal(starting["amount"]) == Decimal("500.00")


def test_a_dated_rate_applies_on_its_day_not_before(client, db, owner):
    staff = make(db, base_daily_wage=Decimal("500"), pay_amount=Decimal("500"))
    tomorrow = date.today() + timedelta(days=1)
    assert add_rate(client, staff, "daily", "650", tomorrow).status_code == 201
    db.refresh(staff)
    assert staff.base_daily_wage == Decimal("500.00")  # not yet
    hist = wage_service.load_history(db, staff)
    assert wage_service.rate_on(hist, staff, date.today()).amount == Decimal("500")
    assert wage_service.rate_on(hist, staff, tomorrow).amount == Decimal("650")
    wage_service.refresh_current(db, staff, tomorrow)
    assert staff.base_daily_wage == Decimal("650.00")


def test_rate_in_force_on_each_day_for_daily_pay(client, db, owner):
    staff = make(db, base_daily_wage=Decimal("500"), pay_amount=Decimal("500"))
    add_rate(client, staff, "daily", "500", date(2026, 1, 1))
    add_rate(client, staff, "daily", "650", date(2026, 2, 10))
    assert wage_service.daily_rate_for(db, staff, date(2026, 2, 9)) == Decimal("500")
    assert wage_service.daily_rate_for(db, staff, date(2026, 2, 10)) == Decimal("650")
    assert wage_service.daily_rate_for(db, staff, date(2026, 2, 28)) == Decimal("650")


def test_no_backdating_before_the_last_payout(client, db, owner):
    staff = make(db, base_daily_wage=Decimal("500"), pay_amount=Decimal("500"))
    mark(client, staff, d(2), "present", "500")
    paid = client.post(
        "/payroll/pay", json={"user_id": str(staff.id), "through": str(d(5))}
    )
    assert paid.status_code == 201, paid.text
    resp = add_rate(client, staff, "daily", "700", d(5))
    assert resp.status_code == 409
    assert "Wages up to 2026-03-05 are already paid" in resp.json()["detail"]
    assert add_rate(client, staff, "daily", "700", d(3)).status_code == 409
    assert add_rate(client, staff, "daily", "700", d(6)).status_code == 201


def test_old_route_edit_writes_a_history_row_once(client, db, owner):
    staff = make(db, base_daily_wage=Decimal("500"))
    resp = client.patch(f"/auth/users/{staff.id}", json={"base_daily_wage": "550"})
    assert resp.status_code == 200
    assert Decimal(resp.json()["base_daily_wage"]) == Decimal("550.00")
    assert resp.json()["pay_type"] == "daily"
    rows = client.get(f"/people/users/{staff.id}/wages").json()
    assert [Decimal(r["amount"]) for r in rows][:2] == [Decimal("550"), Decimal("500")]
    # the staff form sends the same figure again with other edits: no new row
    client.patch(
        f"/auth/users/{staff.id}", json={"base_daily_wage": "550", "phone": "9"}
    )
    assert len(client.get(f"/people/users/{staff.id}/wages").json()) == 2
    # and a new pay type through the same route is recorded too
    resp = client.patch(
        f"/auth/users/{staff.id}", json={"pay_type": "weekly", "pay_amount": "4200"}
    )
    assert resp.status_code == 200
    db.refresh(staff)
    assert staff.pay_type == "weekly" and staff.base_daily_wage == Decimal("600.00")
    assert client.get(f"/people/users/{staff.id}/wages").json()[0]["pay_type"] == (
        "weekly"
    )


def test_hourly_pay_is_rate_times_hours_and_past_days_do_not_move(client, db, owner):
    staff = make(db, base_daily_wage=Decimal("0"))
    add_rate(client, staff, "hourly", "100", date.today())
    db.refresh(staff)
    resp = client.post(f"/attendance/check-in?user_id={staff.id}")
    assert resp.status_code == 200
    att_id = resp.json()["id"]
    row = db.get(Attendance, att_id)
    row.check_in = datetime.utcnow() - timedelta(hours=9)
    db.commit()
    out = client.post(f"/attendance/check-out?attendance_id={att_id}")
    assert out.status_code == 200
    wage = db.query(DailyWage).filter(DailyWage.attendance_id == att_id).one()
    assert wage.amount == Decimal("900.00")
    assert summary_row(client, staff)["pending_daily"] == "900.00"
    # a raise from tomorrow leaves today's pay alone
    add_rate(client, staff, "hourly", "150", date.today() + timedelta(days=1))
    db.refresh(wage)
    assert wage.amount == Decimal("900.00")
    assert summary_row(client, staff)["pending_daily"] == "900.00"


def test_monthly_pay_counts_every_payable_day(client, db, owner):
    shift = client.post(
        "/shifts",
        json={
            "name": f"Day {uuid.uuid4().hex[:5]}",
            "start_time": "09:00:00",
            "end_time": "17:00:00",
            "weekly_off": [6],
        },
    ).json()
    staff = make(db, joining_date=d(1))
    add_rate(client, staff, "monthly", "31000", d(1))
    assert (
        client.put(
            f"/people/users/{staff.id}/shift", json={"shift_id": shift["id"]}
        ).status_code
        == 200
    )
    client.post("/holidays", json={"date": str(d(4)), "name": "Ugadi", "paid": True})
    paid_type = new_leave_type(client, paid=True)
    unpaid_type = new_leave_type(client, paid=False)
    mark(client, staff, d(2), "present")  # Monday
    mark(client, staff, d(3), "half-day")  # Tuesday
    # d(1) Sunday off, d(4) Wednesday holiday, d(5) Thursday paid leave
    # d(6) Friday absent, d(7) Saturday unpaid leave
    act_as(staff)
    for day, kind in ((d(5), paid_type), (d(7), unpaid_type)):
        r = client.post(
            "/leave/requests",
            json={
                "leave_type_id": kind["id"],
                "from_date": str(day),
                "to_date": str(day),
            },
        )
        assert r.status_code == 201, r.text
        ids = r.json()["id"]
        act_as(owner)
        assert client.post(f"/leave/requests/{ids}/approve", json={}).status_code == 200
        act_as(staff)
    act_as(owner)
    mark(client, staff, d(6), "absent")

    # Sun 1000 + Mon 1000 + Tue 500 + Wed holiday 1000 + Thu paid leave 1000 = 4500
    pay = client.post(
        "/payroll/pay", json={"user_id": str(staff.id), "through": str(d(7))}
    )
    assert pay.status_code == 201, pay.text
    assert pay.json()["gross_daily"] == "4500.00"
    # nothing is owed twice
    assert (
        client.post(
            "/payroll/pay", json={"user_id": str(staff.id), "through": str(d(7))}
        ).status_code
        == 400
    )


def test_summary_reports_payable_and_unpaid_days(client, db, owner):
    start = date.today() - timedelta(days=3)
    staff = make(db, joining_date=start)
    add_rate(client, staff, "monthly", "31000", start)
    mark(client, staff, start, "present")
    mark(client, staff, start + timedelta(days=1), "half-day")
    row = summary_row(
        client, staff
    )  # four days so far: worked, half, then nothing twice
    assert row["pay_type"] == "monthly"
    assert Decimal(row["payable_days"]) == Decimal("1.5")
    assert Decimal(row["unpaid_days"]) == Decimal("2.5")
    assert Decimal(row["pending_daily"]) > 0


def test_weekly_pay_is_amount_over_seven_per_payable_day(client, db, owner):
    staff = make(db, joining_date=d(9))
    add_rate(client, staff, "weekly", "7000", d(9))
    for n in (9, 10, 11):
        mark(client, staff, d(n), "present")
    pay = client.post(
        "/payroll/pay", json={"user_id": str(staff.id), "through": str(d(11))}
    )
    assert pay.status_code == 201, pay.text
    assert pay.json()["gross_daily"] == "3000.00"
    # the next pay-out starts the day after
    mark(client, staff, d(12), "present")
    mark(client, staff, d(13), "half-day")
    pay = client.post(
        "/payroll/pay", json={"user_id": str(staff.id), "through": str(d(13))}
    )
    assert pay.json()["gross_daily"] == "1500.00"


def test_rate_change_mid_period_pays_each_day_at_its_own_rate(client, db, owner):
    staff = make(db, joining_date=d(9))
    add_rate(client, staff, "weekly", "7000", d(9))
    add_rate(client, staff, "weekly", "14000", d(11))  # 2000 a day from the 11th
    for n in (9, 10, 11, 12):
        mark(client, staff, d(n), "present")
    pay = client.post(
        "/payroll/pay", json={"user_id": str(staff.id), "through": str(d(12))}
    )
    assert pay.json()["gross_daily"] == "6000.00"  # 1000 + 1000 + 2000 + 2000


def test_salaried_cannot_be_paid_into_the_future(client, db, owner):
    staff = make(db, joining_date=d(1))
    add_rate(client, staff, "monthly", "31000", d(1))
    resp = client.post(
        "/payroll/pay",
        json={
            "user_id": str(staff.id),
            "through": str(date.today() + timedelta(days=3)),
        },
    )
    assert resp.status_code == 400


def test_unpaid_leave_is_not_paid_for_a_salaried_person(client, db, owner):
    staff = make(db, joining_date=d(9))
    add_rate(client, staff, "monthly", "31000", d(9))
    kind = new_leave_type(client, paid=False)
    mark(client, staff, d(9), "present")
    act_as(staff)
    r = client.post(
        "/leave/requests",
        json={
            "leave_type_id": kind["id"],
            "from_date": str(d(10)),
            "to_date": str(d(11)),
        },
    )
    act_as(owner)
    assert (
        client.post(f"/leave/requests/{r.json()['id']}/approve", json={}).status_code
        == 200
    )
    pay = client.post(
        "/payroll/pay", json={"user_id": str(staff.id), "through": str(d(11))}
    )
    assert pay.json()["gross_daily"] == "1000.00"  # only the day worked


def test_summary_row_has_the_new_fields(client, db, owner):
    staff = make(db, base_daily_wage=Decimal("500"), employment="part_time")
    mark(client, staff, d(2), "present", "500")
    row = summary_row(client, staff)
    assert row["pay_type"] == "daily" and row["employment"] == "part_time"
    assert row["pending_daily"] == "500.00"
    assert Decimal(row["payable_days"]) == 1
    assert Decimal(row["unpaid_days"]) == 0
    assert "overtime_hours_suggested" in row
    mine = client.get("/payroll/me")  # still works for the signed-in person
    assert mine.status_code == 200


# ------------------------------------------------------------------ H4: leave


FUTURE = date(2027, 5, 3)  # a Monday, well after today


def ask(client, kind, start, end=None, **extra):
    return client.post(
        "/leave/requests",
        json={
            "leave_type_id": kind["id"],
            "from_date": str(start),
            "to_date": str(end or start),
            **extra,
        },
    )


def test_leave_types_are_listed_for_everyone(client, db, owner):
    new_leave_type(client, name=f"Casual {uuid.uuid4().hex[:4]}", yearly_days=12)
    act_as(make(db, "worker"))
    assert client.get("/leave/types").status_code == 200
    assert client.post("/leave/types", json={"name": "x"}).status_code == 403


def test_leave_days_skip_weekly_offs_and_holidays(client, db, owner):
    shift = client.post(
        "/shifts",
        json={
            "name": f"S {uuid.uuid4().hex[:5]}",
            "start_time": "09:00:00",
            "end_time": "17:00:00",
            "weekly_off": [6],
        },
    ).json()
    staff = make(db, shift_id=uuid.UUID(shift["id"]))
    client.post(
        "/holidays", json={"date": str(FUTURE + timedelta(days=2)), "name": "H"}
    )
    kind = new_leave_type(client)
    act_as(staff)
    # Mon 3 May .. Sun 9 May: 7 days, minus Sunday off and Wednesday holiday
    resp = ask(client, kind, FUTURE, FUTURE + timedelta(days=6))
    assert resp.status_code == 201, resp.text
    assert Decimal(resp.json()["days"]) == 5
    assert resp.json()["status"] == "pending"


def test_leave_overlap_is_refused(client, db, owner):
    staff = make(db)
    kind = new_leave_type(client)
    act_as(staff)
    assert ask(client, kind, FUTURE, FUTURE + timedelta(days=2)).status_code == 201
    clash = ask(client, kind, FUTURE + timedelta(days=2), FUTURE + timedelta(days=4))
    assert clash.status_code == 422
    assert ask(client, kind, FUTURE + timedelta(days=3)).status_code == 201


def test_leave_beyond_the_balance_is_refused_with_the_days_left(client, db, owner):
    staff = make(db)
    kind = new_leave_type(client, name=f"Casual {uuid.uuid4().hex[:4]}", yearly_days=3)
    act_as(staff)
    assert ask(client, kind, FUTURE, FUTURE + timedelta(days=1)).status_code == 201
    resp = ask(client, kind, FUTURE + timedelta(days=5), FUTURE + timedelta(days=7))
    assert resp.status_code == 422
    assert resp.json()["detail"] == f"Only 1 days of {kind['name']} left"
    balance = [
        b
        for b in client.get("/leave/balances/me", params={"on": str(FUTURE)}).json()
        if b["leave_type_id"] == kind["id"]
    ][0]
    assert Decimal(balance["pending"]) == 2 and Decimal(balance["left"]) == 1
    assert Decimal(balance["used"]) == 0


def test_half_day_counts_half_and_only_for_one_day(client, db, owner):
    staff = make(db)
    kind = new_leave_type(client, yearly_days=1)
    act_as(staff)
    ok = ask(client, kind, FUTURE, half_day=True)
    assert ok.status_code == 201 and Decimal(ok.json()["days"]) == Decimal("0.5")
    bad = ask(
        client,
        kind,
        FUTURE + timedelta(days=7),
        FUTURE + timedelta(days=8),
        half_day=True,
    )
    assert bad.status_code == 422


def test_approving_writes_leave_into_attendance(client, db, owner):
    staff = make(db, base_daily_wage=Decimal("500"))
    kind = new_leave_type(client, paid=True)
    act_as(staff)
    req = ask(client, kind, FUTURE, FUTURE + timedelta(days=1)).json()
    act_as(owner)
    resp = client.post(f"/leave/requests/{req['id']}/approve", json={"note": "ok"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "approved" and resp.json()["decision_note"] == "ok"
    rows = client.get(
        "/attendance/all",
        params={
            "user_id": str(staff.id),
            "start_date": str(FUTURE),
            "end_date": str(FUTURE + timedelta(days=1)),
        },
    ).json()
    assert [r["status"] for r in rows] == ["leave", "leave"]
    assert rows[0]["leave_type_id"] == kind["id"] and rows[0]["paid"] is True
    assert rows[0]["check_in"] is None
    # approved days now count as used
    act_as(staff)
    used = [
        b
        for b in client.get("/leave/balances/me", params={"on": str(FUTURE)}).json()
        if b["leave_type_id"] == kind["id"]
    ][0]
    assert Decimal(used["used"]) == 2


def test_nobody_decides_their_own_request(client, db, owner):
    manager = make(db, "manager")
    kind = new_leave_type(client)
    act_as(manager)
    req = ask(client, kind, FUTURE).json()
    # the manager holds the approve permission, but not over their own request
    assert (
        client.post(f"/leave/requests/{req['id']}/approve", json={}).status_code == 403
    )
    assert (
        client.post(
            f"/leave/requests/{req['id']}/reject", json={"note": "no"}
        ).status_code
        == 403
    )
    act_as(owner)
    assert (
        client.post(f"/leave/requests/{req['id']}/approve", json={}).status_code == 200
    )
    # decided once
    assert (
        client.post(f"/leave/requests/{req['id']}/approve", json={}).status_code == 409
    )


def test_reject_needs_a_note_and_frees_nothing_in_attendance(client, db, owner):
    staff = make(db)
    kind = new_leave_type(client)
    act_as(staff)
    req = ask(client, kind, FUTURE).json()
    act_as(owner)
    assert (
        client.post(f"/leave/requests/{req['id']}/reject", json={}).status_code == 422
    )
    resp = client.post(
        f"/leave/requests/{req['id']}/reject", json={"note": "Busy week"}
    )
    assert resp.status_code == 200 and resp.json()["status"] == "rejected"
    rows = client.get("/attendance/all", params={"user_id": str(staff.id)}).json()
    assert rows == []


def test_cancel_pending_and_future_approved_but_not_started(client, db, owner):
    staff = make(db)
    kind = new_leave_type(client)
    act_as(staff)
    pending = ask(client, kind, FUTURE).json()
    assert (
        client.post(f"/leave/requests/{pending['id']}/cancel").json()["status"]
        == "cancelled"
    )
    approved = ask(client, kind, FUTURE + timedelta(days=7)).json()
    act_as(owner)
    client.post(f"/leave/requests/{approved['id']}/approve", json={})
    assert db.query(Attendance).filter(Attendance.user_id == staff.id).count() == 1
    # someone else cannot cancel it
    assert client.post(f"/leave/requests/{approved['id']}/cancel").status_code == 403
    act_as(staff)
    assert client.post(f"/leave/requests/{approved['id']}/cancel").status_code == 200
    db.expire_all()
    assert db.query(Attendance).filter(Attendance.user_id == staff.id).count() == 0
    # the days are free to ask for again
    assert ask(client, kind, FUTURE + timedelta(days=7)).status_code == 201


def test_cancelling_leave_that_has_started_is_refused(client, db, owner):
    staff = make(db)
    kind = new_leave_type(client)
    act_as(staff)
    req = ask(client, kind, date.today()).json()
    act_as(owner)
    client.post(f"/leave/requests/{req['id']}/approve", json={})
    act_as(staff)
    assert client.post(f"/leave/requests/{req['id']}/cancel").status_code == 409


def test_who_sees_which_requests_and_balances(client, db, owner):
    a, b = make(db), make(db)
    manager = make(db, "manager")
    kind = new_leave_type(client)
    act_as(a)
    ask(client, kind, FUTURE)
    act_as(b)
    ask(client, kind, FUTURE)
    mine = client.get("/leave/requests").json()
    assert {r["user_id"] for r in mine} == {str(b.id)}
    # naming someone else does not widen what a worker sees
    assert {
        r["user_id"]
        for r in client.get("/leave/requests", params={"user_id": str(a.id)}).json()
    } == {str(b.id)}
    assert (
        client.get("/leave/balances", params={"user_id": str(a.id)}).status_code == 403
    )
    assert (
        client.get("/leave/balances", params={"user_id": str(b.id)}).status_code == 200
    )
    act_as(manager)
    everyone = client.get("/leave/requests", params={"status": "pending"}).json()
    assert {str(a.id), str(b.id)} <= {r["user_id"] for r in everyone}
    only_a = client.get("/leave/requests", params={"user_id": str(a.id)}).json()
    assert {r["user_id"] for r in only_a} == {str(a.id)}
    assert (
        client.get("/leave/balances", params={"user_id": str(a.id)}).status_code == 200
    )
    window = client.get(
        "/leave/requests",
        params={
            "from": str(FUTURE + timedelta(days=30)),
            "to": str(FUTURE + timedelta(days=40)),
        },
    ).json()
    assert window == []


def test_a_worker_cannot_approve(client, db, owner):
    staff = make(db)
    other = make(db)
    kind = new_leave_type(client)
    act_as(other)
    req = ask(client, kind, FUTURE).json()
    act_as(staff)
    assert (
        client.post(f"/leave/requests/{req['id']}/approve", json={}).status_code == 403
    )


# ------------------------------------------------- attendance: leave + holidays


def test_attendance_sheet_carries_holidays_and_leave(client, db, owner):
    staff = make(db, joining_date=d(1), base_daily_wage=Decimal("500"))
    client.post("/holidays", json={"date": str(d(4)), "name": "Ugadi", "paid": False})
    kind = new_leave_type(client)
    mark(client, staff, d(2), "present", "500")
    act_as(staff)
    req = ask(client, kind, d(5)).json()
    act_as(owner)
    client.post(f"/leave/requests/{req['id']}/approve", json={})
    rows = client.get(
        "/attendance/all",
        params={
            "user_id": str(staff.id),
            "start_date": str(d(1)),
            "end_date": str(d(7)),
        },
    ).json()
    by_day = {r["date"]: r for r in rows}
    assert by_day[str(d(2))]["status"] == "present"
    assert by_day[str(d(4))]["status"] == "holiday"
    assert by_day[str(d(4))]["holiday_name"] == "Ugadi"
    assert by_day[str(d(4))]["paid"] is False
    assert by_day[str(d(4))]["check_in"] is None
    assert by_day[str(d(5))]["status"] == "leave"
    # without a date range nothing new appears: the old answer is unchanged
    plain = client.get("/attendance/all", params={"user_id": str(staff.id)}).json()
    assert {r["status"] for r in plain} == {"present", "leave"}
    # my own sheet, as the person
    act_as(staff)
    mine = client.get(
        "/attendance/my", params={"start_date": str(d(1)), "end_date": str(d(7))}
    ).json()
    assert {r["status"] for r in mine} == {"present", "holiday", "leave"}
    assert len(client.get("/attendance/my").json()) == 2


def test_leave_status_cannot_be_marked_by_hand(client, db, owner):
    staff = make(db)
    resp = client.post(
        "/attendance/",
        json={
            "user_id": str(staff.id),
            "date": str(d(2)),
            "status": "leave",
            "daily_wage": "0",
        },
    )
    assert resp.status_code == 422


# ---------------------------------------------------------- H5: shifts, holidays


def new_shift(client, **kw):
    body = {
        "name": f"Shift {uuid.uuid4().hex[:6]}",
        "start_time": "09:00:00",
        "end_time": "17:00:00",
        "grace_minutes": 10,
        "weekly_off": [6],
        **kw,
    }
    resp = client.post("/shifts", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_shift_crud_and_delete_is_refused_while_in_use(client, db, owner):
    shift = new_shift(client)
    assert shift["grace_minutes"] == 10 and shift["weekly_off"] == [6]
    changed = client.put(
        f"/shifts/{shift['id']}",
        json={**shift, "name": shift["name"] + "!", "end_time": "18:00:00"},
    )
    assert changed.status_code == 200 and changed.json()["end_time"] == "18:00:00"
    assert (
        client.post(
            "/shifts", json={**shift, "name": changed.json()["name"]}
        ).status_code
        == 409
    )
    staff = make(db)
    assigned = client.put(
        f"/people/users/{staff.id}/shift", json={"shift_id": shift["id"]}
    )
    assert assigned.status_code == 200 and assigned.json()["shift_id"] == shift["id"]
    assert client.delete(f"/shifts/{shift['id']}").status_code == 409
    client.put(f"/people/users/{staff.id}/shift", json={"shift_id": None})
    assert client.delete(f"/shifts/{shift['id']}").status_code == 204
    assert client.get("/shifts").status_code == 200


def test_bad_weekdays_are_refused(client, owner):
    resp = client.post(
        "/shifts",
        json={
            "name": "x",
            "start_time": "09:00:00",
            "end_time": "17:00:00",
            "weekly_off": [7],
        },
    )
    assert resp.status_code == 422


def test_shift_permissions(client, db, owner):
    new_shift(client)
    act_as(make(db, "worker"))
    assert client.get("/shifts").status_code == 200
    assert (
        client.post(
            "/shifts",
            json={"name": "n", "start_time": "09:00:00", "end_time": "17:00:00"},
        ).status_code
        == 403
    )
    act_as(make(db, "manager"))
    assert (
        client.post(
            "/shifts",
            json={
                "name": f"m{uuid.uuid4().hex[:5]}",
                "start_time": "09:00:00",
                "end_time": "17:00:00",
            },
        ).status_code
        == 201
    )


def test_late_and_early_minutes_follow_the_shift(client, db, owner):
    staff = make(db, base_daily_wage=Decimal("500"))
    shift = new_shift(client)  # 09:00 to 17:00, 10 minutes grace
    client.put(f"/people/users/{staff.id}/shift", json={"shift_id": shift["id"]})
    # workshop time is UTC+5:30: 09:25 local is 03:55 UTC, 16:30 local is 11:00 UTC
    late = mark(
        client,
        staff,
        d(2),
        "present",
        "500",
        check_in=f"{d(2)}T03:55:00",
        check_out=f"{d(2)}T11:00:00",
    )
    assert late["late_minutes"] == 25 and late["early_leave_minutes"] == 30
    # 09:05 local is inside the grace; 17:20 local is past the end
    on_time = mark(
        client,
        staff,
        d(3),
        "present",
        "500",
        check_in=f"{d(3)}T03:35:00",
        check_out=f"{d(3)}T11:50:00",
    )
    assert on_time["late_minutes"] == 0 and on_time["early_leave_minutes"] == 0
    # no shift, no minutes
    other = make(db, base_daily_wage=Decimal("500"))
    row = mark(
        client,
        other,
        d(2),
        "present",
        "500",
        check_in=f"{d(2)}T03:55:00",
        check_out=f"{d(2)}T11:00:00",
    )
    assert row["late_minutes"] == 0 and row["early_leave_minutes"] == 0


def test_overtime_is_suggested_never_paid(client, db, owner):
    staff = make(db, base_daily_wage=Decimal("500"), pay_amount=Decimal("500"))
    shift = new_shift(client)
    client.put(f"/people/users/{staff.id}/shift", json={"shift_id": shift["id"]})
    # leaves 18:30 local (13:00 UTC): 1.5 hours past 17:00
    mark(
        client,
        staff,
        d(2),
        "present",
        "500",
        check_in=f"{d(2)}T03:30:00",
        check_out=f"{d(2)}T13:00:00",
    )
    row = summary_row(client, staff)
    assert row["overtime_hours_suggested"] == "1.50"
    assert row["pending_daily"] == "500.00"  # not a rupee more
    assert row["pending_extras"] == "0.00"


def test_holiday_crud(client, db, owner):
    created = client.post(
        "/holidays", json={"date": "2026-01-26", "name": "Republic Day"}
    )
    assert created.status_code == 201 and created.json()["paid"] is True
    assert (
        client.post(
            "/holidays", json={"date": "2026-01-26", "name": "Again"}
        ).status_code
        == 409
    )
    client.post(
        "/holidays", json={"date": "2027-01-26", "name": "Republic Day", "paid": False}
    )
    assert [
        h["date"] for h in client.get("/holidays", params={"year": 2026}).json()
    ] == ["2026-01-26"]
    assert len(client.get("/holidays").json()) == 2
    assert client.delete(f"/holidays/{created.json()['id']}").status_code == 204
    assert client.delete(f"/holidays/{created.json()['id']}").status_code == 404
    act_as(make(db, "worker"))
    assert client.get("/holidays").status_code == 200
    assert (
        client.post("/holidays", json={"date": "2026-08-15", "name": "x"}).status_code
        == 403
    )
    act_as(make(db, "manager"))
    assert (
        client.post(
            "/holidays", json={"date": "2026-08-15", "name": "Independence Day"}
        ).status_code
        == 201
    )


# -------------------------------------------------------------- H6: profile


def test_profile_fields_round_trip_and_never_hold_a_full_id(client, db, owner):
    staff = make(db)
    ok = client.patch(
        f"/auth/users/{staff.id}",
        json={
            "joining_date": "2026-02-01",
            "address": "Channapatna",
            "emergency_contact_name": "Lakshmi",
            "emergency_contact_phone": "9876543210",
            "pay_upi": "ravi@okbank",
            "id_proof_type": "aadhaar",
            "id_proof_last4": "4321",
            "employment": "part_time",
        },
    )
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert body["id_proof_last4"] == "4321" and body["employment"] == "part_time"
    assert body["joining_date"] == "2026-02-01" and body["pay_upi"] == "ravi@okbank"
    for long_number in ("123456789012", "12345"):
        bad = client.patch(
            f"/auth/users/{staff.id}", json={"id_proof_last4": long_number}
        )
        assert bad.status_code == 422
    db.refresh(staff)
    assert staff.id_proof_last4 == "4321"
    assert (
        client.patch(
            f"/auth/users/{staff.id}", json={"pay_upi": "not a upi"}
        ).status_code
        == 422
    )
    assert (
        client.patch(
            f"/auth/users/{staff.id}", json={"id_proof_type": "passport"}
        ).status_code
        == 422
    )
    cleared = client.patch(f"/auth/users/{staff.id}", json={"pay_upi": ""})
    assert cleared.json()["pay_upi"] is None
    # an account can be created with the new fields too
    tag = uuid.uuid4().hex[:6]
    made = client.post(
        "/auth/register",
        json={
            "username": f"new_{tag}",
            "email": f"new_{tag}@example.com",
            "role": "worker",
            "password": "longenough1",
            "pay_type": "monthly",
            "pay_amount": "20000",
            "joining_date": "2026-03-01",
            "id_proof_last4": "9999",
        },
    )
    assert made.status_code == 200, made.text
    assert made.json()["pay_type"] == "monthly"
    rows = client.get(f"/people/users/{made.json()['id']}/wages").json()
    assert len(rows) == 1 and rows[0]["effective_from"] == "2026-03-01"
    too_long = client.post(
        "/auth/register",
        json={
            "username": f"x_{tag}",
            "email": f"x_{tag}@example.com",
            "role": "worker",
            "password": "longenough1",
            "id_proof_last4": "123456789012",
        },
    )
    assert too_long.status_code == 422


def test_pay_and_id_fields_hide_from_people_without_read_pay(client, db, owner):
    staff = make(
        db,
        pay_upi="a@bank",
        id_proof_type="pan",
        id_proof_last4="1234",
        address="Mysuru",
        base_daily_wage=Decimal("500"),
        pay_amount=Decimal("500"),
    )
    act_as(make(db, "sales"))
    row = next(u for u in client.get("/auth/").json() if u["id"] == str(staff.id))
    assert row["address"] == "Mysuru"  # the profile is readable with people:user:read
    for hidden in (
        "pay_upi",
        "id_proof_type",
        "id_proof_last4",
        "pay_type",
        "pay_amount",
        "base_daily_wage",
    ):
        assert row[hidden] is None
    act_as(make(db, "manager"))
    row = next(u for u in client.get("/auth/").json() if u["id"] == str(staff.id))
    assert row["pay_upi"] == "a@bank" and row["id_proof_last4"] == "1234"


def test_wage_history_permissions(client, db, owner):
    staff = make(db, base_daily_wage=Decimal("500"))
    act_as(make(db, "manager"))
    assert client.get(f"/people/users/{staff.id}/wages").status_code == 200
    assert add_rate(client, staff, "daily", "900", date.today()).status_code == 403
    act_as(make(db, "worker"))
    assert client.get(f"/people/users/{staff.id}/wages").status_code == 403
    act_as(make(db, "sales"))
    assert client.get(f"/people/users/{staff.id}/wages").status_code == 403


# ---------------------------------------------------------------- documents


def upload(client, store, staff, kind="id_proof", content_type="image/png", size=2048):
    resp = client.post(
        f"/people/users/{staff.id}/documents/upload-url",
        json={
            "kind": kind,
            "filename": "aadhaar scan.png",
            "content_type": content_type,
            "size": size,
        },
    )
    return resp


def test_document_upload_confirm_list_delete(client, db, owner, store):
    staff = make(db)
    resp = upload(client, store, staff)
    assert resp.status_code == 201, resp.text
    ticket = resp.json()
    assert ticket["headers"]["Content-Type"] == "image/png"
    assert ticket["upload_url"].startswith("https://store.invalid/")
    # not listed (and not confirmable) until the file is in the bucket
    assert client.get(f"/people/users/{staff.id}/documents").json() == []
    nothing = client.post(
        f"/people/users/{staff.id}/documents/{ticket['document_id']}/confirm"
    )
    assert nothing.status_code == 409
    key, _, _ = store.signed[-1]
    assert key.startswith(f"staff/{staff.id}/")
    store.objects[key] = b"x" * 2048
    done = client.post(
        f"/people/users/{staff.id}/documents/{ticket['document_id']}/confirm"
    )
    assert done.status_code == 200 and done.json()["url"].startswith(
        "https://store.invalid/"
    )
    rows = client.get(f"/people/users/{staff.id}/documents").json()
    assert len(rows) == 1 and rows[0]["kind"] == "id_proof" and rows[0]["size"] == 2048
    assert rows[0]["url"] and "storage_key" not in rows[0]
    assert (
        client.delete(
            f"/people/users/{staff.id}/documents/{ticket['document_id']}"
        ).status_code
        == 204
    )
    assert key not in store.objects
    assert client.get(f"/people/users/{staff.id}/documents").json() == []


def test_documents_only_images_and_pdf_under_ten_mb(client, db, owner, store):
    staff = make(db)
    assert (
        upload(client, store, staff, content_type="application/pdf").status_code == 201
    )
    assert upload(client, store, staff, content_type="text/html").status_code == 422
    assert (
        upload(client, store, staff, content_type="application/zip").status_code == 422
    )
    assert upload(client, store, staff, size=10 * 1024 * 1024).status_code == 201
    assert upload(client, store, staff, size=10 * 1024 * 1024 + 1).status_code == 422
    assert upload(client, store, staff, size=0).status_code == 422
    assert upload(client, store, staff, kind="selfie").status_code == 422


def test_document_with_the_wrong_size_is_thrown_away(client, db, owner, store):
    staff = make(db)
    ticket = upload(client, store, staff, size=2048).json()
    key, _, _ = store.signed[-1]
    store.objects[key] = b"x" * 10
    resp = client.post(
        f"/people/users/{staff.id}/documents/{ticket['document_id']}/confirm"
    )
    assert resp.status_code == 422
    assert key not in store.objects


def test_document_permissions_manager_reads_owner_writes(client, db, owner, store):
    staff = make(db)
    ticket = upload(client, store, staff).json()
    store.objects[store.signed[-1][0]] = b"x" * 2048
    client.post(f"/people/users/{staff.id}/documents/{ticket['document_id']}/confirm")
    act_as(make(db, "manager"))
    assert client.get(f"/people/users/{staff.id}/documents").status_code == 200
    assert upload(client, store, staff).status_code == 403
    assert (
        client.delete(
            f"/people/users/{staff.id}/documents/{ticket['document_id']}"
        ).status_code
        == 403
    )
    act_as(make(db, "worker"))
    assert client.get(f"/people/users/{staff.id}/documents").status_code == 403
    # a document belongs to one person
    act_as(owner)
    stranger = make(db)
    assert (
        client.delete(
            f"/people/users/{stranger.id}/documents/{ticket['document_id']}"
        ).status_code
        == 404
    )


# ---------------------------------------------------------------- offboarding


def test_offboard_returns_the_final_settlement(client, db, owner):
    staff = make(db, base_daily_wage=Decimal("500"), pay_amount=Decimal("500"))
    mark(client, staff, d(2), "present", "500")
    mark(client, staff, d(3), "present", "500")
    mark(
        client, staff, d(20), "present", "500"
    )  # after the last day: not in the settlement
    client.post(
        "/payroll/work-entries",
        json={
            "user_id": str(staff.id),
            "work_date": str(d(2)),
            "kind": "bonus",
            "description": "Diwali",
            "rate": "300",
        },
    )
    client.post(
        "/payroll/advances",
        json={"user_id": str(staff.id), "given_on": str(d(1)), "amount": "400"},
    )
    resp = client.post(
        f"/people/users/{staff.id}/offboard",
        json={"last_working_date": str(d(10)), "note": "Moving away"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["pending_daily"] == "1000.00" and body["pending_extras"] == "300.00"
    assert body["advance_balance"] == "400.00"
    assert body["net_to_pay"] == "900.00"
    assert body["deactivates_on"] == str(d(11)) and body["is_active"] is False
    db.refresh(staff)
    assert staff.last_working_date == d(10) and staff.is_active is False
    # the owner then pays it with the ordinary pay-out
    pay = client.post(
        "/payroll/pay",
        json={"user_id": str(staff.id), "through": str(d(10)), "deduction": "400"},
    )
    assert pay.status_code == 201 and pay.json()["net_paid"] == "900.00"


def test_offboarding_with_a_future_last_day_keeps_the_account_until_then(
    client, db, owner
):
    staff = make(db)
    last = date.today() + timedelta(days=5)
    resp = client.post(
        f"/people/users/{staff.id}/offboard", json={"last_working_date": str(last)}
    )
    assert resp.status_code == 200
    assert resp.json()["is_active"] is True and resp.json()["deactivates_on"] == str(
        last + timedelta(days=1)
    )
    db.refresh(staff)
    assert staff.is_active is True


def test_offboard_is_owner_only_and_not_for_yourself(client, db, owner):
    staff = make(db)
    assert (
        client.post(
            f"/people/users/{owner.id}/offboard", json={"last_working_date": str(d(1))}
        ).status_code
        == 400
    )
    act_as(make(db, "manager"))
    assert (
        client.post(
            f"/people/users/{staff.id}/offboard", json={"last_working_date": str(d(1))}
        ).status_code
        == 403
    )
    act_as(owner)
    assert (
        client.post(
            f"/people/users/{uuid.uuid4()}/offboard",
            json={"last_working_date": str(d(1))},
        ).status_code
        == 404
    )


def test_a_salaried_settlement_covers_pay_to_the_last_day(client, db, owner):
    staff = make(db, joining_date=d(9))
    add_rate(client, staff, "weekly", "7000", d(9))
    for n in (9, 10, 11):
        mark(client, staff, d(n), "present")
    resp = client.post(
        f"/people/users/{staff.id}/offboard", json={"last_working_date": str(d(11))}
    )
    assert resp.json()["pending_daily"] == "3000.00"
    assert resp.json()["net_to_pay"] == "3000.00"


def test_an_offboarded_person_is_locked_out_the_day_after(client, db):
    from app.core.security import create_access_token

    tag = uuid.uuid4().hex[:6]
    staff = make(db, last_working_date=date.today() - timedelta(days=1))
    token = create_access_token(
        {"sub": str(staff.id), "username": staff.username, "tv": 0}
    )
    app.dependency_overrides.pop(get_current_user, None)
    resp = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401
    staff.last_working_date = date.today()
    db.commit()
    assert (
        client.get("/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code
        == 200
    )
    assert tag


# ------------------------------------------------------- old behaviour intact


def test_old_daily_flow_is_unchanged(client, db, owner):
    staff = make(db, base_daily_wage=Decimal("800"))
    resp = client.post(f"/attendance/check-in?user_id={staff.id}")
    assert resp.status_code == 200
    assert Decimal(resp.json()["daily_wage"]) == Decimal("800.00")
    assert resp.json()["late_minutes"] == 0
    mark(client, staff, d(2), "present", "800")
    mark(client, staff, d(3), "half-day", "800")
    row = summary_row(client, staff)
    assert row["pending_daily"] == "1200.00" and row["owed_gross"] == "1200.00"
    pay = client.post(
        "/payroll/pay", json={"user_id": str(staff.id), "through": str(d(31))}
    )
    assert pay.status_code == 201 and pay.json()["gross_daily"] == "1200.00"
    patched = client.patch(
        f"/attendance/{mark(client, staff, d(4), 'present', '800')['id']}",
        json={"status": "half-day"},
    )
    assert patched.status_code == 200
    wage = (
        db.query(DailyWage)
        .join(Attendance)
        .filter(Attendance.user_id == staff.id, Attendance.date == d(4))
        .one()
    )
    assert wage.amount == Decimal("400.00")
    assert (
        db.query(PayrollPayment).filter(PayrollPayment.user_id == staff.id).count() == 1
    )
    assert (
        db.query(WorkerAdvance).filter(WorkerAdvance.user_id == staff.id).count() == 0
    )
    assert db.query(UserWage).filter(UserWage.user_id == staff.id).count() == 0


def test_options_list_everything_the_screens_need(client, db, owner):
    kind = new_leave_type(client, name=f"Opt {uuid.uuid4().hex[:5]}", yearly_days=5)
    shift = new_shift(client, weekly_off=[6, 2])
    client.post(
        "/holidays", json={"date": f"{date.today().year}-12-25", "name": "Christmas"}
    )
    act_as(make(db, "worker"))  # anyone signed in
    body = client.get("/people/options").json()
    assert [p["value"] for p in body["pay_types"]] == [
        "daily",
        "weekly",
        "monthly",
        "hourly",
    ]
    assert (
        body["pay_types"][2]["label"] == "Monthly salary"
        and body["pay_types"][2]["unit"] == "per month"
    )
    assert [e["value"] for e in body["employment"]] == ["full_time", "part_time"]
    assert {i["value"] for i in body["id_proof_types"]} == {
        "aadhaar",
        "pan",
        "voter",
        "driving",
        "other",
    }
    assert {k["value"] for k in body["document_kinds"]} == {
        "id_proof",
        "contract",
        "photo",
        "other",
    }
    assert (
        body["document_max_bytes"] == 10 * 1024 * 1024
        and "application/pdf" in body["document_content_types"]
    )
    assert (
        body["weekdays"][0] == {"value": 0, "label": "Monday"}
        and len(body["weekdays"]) == 7
    )
    assert kind["id"] in {t["id"] for t in body["leave_types"]}
    assert [s for s in body["shifts"] if s["id"] == shift["id"]][0]["weekly_off"] == [
        2,
        6,
    ]
    assert "Christmas" in {h["name"] for h in body["holidays"]}
