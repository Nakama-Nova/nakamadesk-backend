"""
Checking yourself in and out with GPS, and who reports to whom: the manager sees the
people under them, the owner sees everyone, and nobody else sees anyone.
"""

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.core.security import create_access_token
from app.models.attendance import Attendance
from app.models.daily_wage import DailyWage
from app.models.plant_unit import PlantUnit
from app.models.user import User
from tests.test_plant_units import LAT, LON, NORTH, make_unit


@pytest.fixture(autouse=True)
def only_my_units(db):
    """The shared test DB keeps every unit ever made: switch them off first."""
    db.query(PlantUnit).update({PlantUnit.is_active: False})
    db.commit()


@pytest.fixture
def staff(db):
    """Factory: staff("worker", wage=700) -> (user, auth headers); a real login token."""

    def make(role="worker", wage="700.00", boss=None):
        uid = uuid.uuid4().hex[:8]
        u = User(
            username=f"{role}_{uid}",
            email=f"{role}_{uid}@example.com",
            password_hash="x",
            role=role,
            is_active=True,
            base_daily_wage=Decimal(wage),
            reports_to_id=boss.id if boss else None,
        )
        db.add(u)
        db.commit()
        db.refresh(u)
        token = create_access_token({"sub": str(u.id)})
        return u, {"Authorization": f"Bearer {token}"}

    return make


def at(lat=LAT, lon=LON, acc=10):
    return {"latitude": lat, "longitude": lon, "accuracy_m": acc}


def backdate(db, att_id, hours):
    row = db.get(Attendance, att_id)
    row.check_in = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(
        hours=hours
    )
    db.commit()
    db.expire_all()


# ------------------------------------------------------------ checking myself in
def test_check_in_at_the_workshop_is_matched_to_its_unit(client, db, staff):
    unit = make_unit(db, "MFG")
    worker, h = staff(wage="700.00")
    r = client.post("/attendance/me/check-in", headers=h, json=at())
    assert r.status_code == 200, r.text
    a = r.json()
    assert a["location_status"] == "on_site" and a["plant_unit_id"] == str(unit.id)
    assert a["recorded_by"] == str(worker.id) and a["user_id"] == str(worker.id)
    assert a["check_in"] and a["check_out"] is None
    assert Decimal(str(a["daily_wage"])) == Decimal("700.00")  # the person's own wage


def test_check_in_away_from_every_unit_is_recorded_not_refused(client, db, staff):
    make_unit(db, "MFG")
    _, h = staff()
    r = client.post("/attendance/me/check-in", headers=h, json=at(lat=NORTH, acc=5))
    assert r.status_code == 200
    assert (
        r.json()["location_status"] == "off_site" and r.json()["plant_unit_id"] is None
    )


def test_a_vague_or_impossible_position_is_refused(client, db, staff):
    make_unit(db, "MFG")
    _, h = staff()
    assert (
        client.post("/attendance/me/check-in", headers=h, json=at(acc=500)).status_code
        == 422
    )
    assert (
        client.post("/attendance/me/check-in", headers=h, json=at(lat=95)).status_code
        == 422
    )
    assert (
        client.post(
            "/attendance/me/check-in", headers=h, json={"latitude": 1}
        ).status_code
        == 422
    )
    assert client.post("/attendance/me/check-in", json=at()).status_code == 401


def test_the_client_cannot_choose_the_unit_or_the_wage(client, db, staff):
    unit = make_unit(db, "MFG", lat=NORTH)  # the worker is NOT in this one
    _, h = staff(wage="700.00")
    r = client.post(
        "/attendance/me/check-in",
        headers=h,
        json={**at(), "plant_unit_id": str(unit.id), "daily_wage": 99999},
    )
    assert r.status_code == 200
    assert r.json()["plant_unit_id"] is None
    assert Decimal(str(r.json()["daily_wage"])) == Decimal("700.00")


def test_one_record_a_day_and_double_taps_make_one(client, db, staff):
    make_unit(db, "MFG")
    worker, h = staff()
    first = client.post("/attendance/me/check-in", headers=h, json=at())
    again = client.post("/attendance/me/check-in", headers=h, json=at())
    assert first.status_code == 200 and again.status_code == 400
    assert db.query(Attendance).filter(Attendance.user_id == worker.id).count() == 1


# ---------------------------------------------------------------- checking out
def test_check_out_closes_the_day_with_hours_wage_and_position(client, db, staff):
    unit = make_unit(db, "MFG")
    worker, h = staff(wage="700.00")
    att = client.post("/attendance/me/check-in", headers=h, json=at()).json()
    backdate(db, att["id"], 9)
    r = client.post("/attendance/me/check-out", headers=h, json=at())
    assert r.status_code == 200, r.text
    a = r.json()
    assert a["status"] == "present" and Decimal(str(a["total_hours"])) >= 9
    assert a["check_out_location_status"] == "on_site"
    assert a["check_out_plant_unit_id"] == str(unit.id)
    wage = db.query(DailyWage).filter(DailyWage.attendance_id == att["id"]).one()
    assert wage.amount == Decimal("700.00") and wage.payment_status == "pending"


def test_check_out_needs_a_check_in_and_works_once(client, db, staff):
    make_unit(db, "MFG")
    _, h = staff()
    assert (
        client.post("/attendance/me/check-out", headers=h, json=at()).status_code == 400
    )
    client.post("/attendance/me/check-in", headers=h, json=at())
    assert (
        client.post("/attendance/me/check-out", headers=h, json=at()).status_code == 200
    )
    assert (
        client.post("/attendance/me/check-out", headers=h, json=at()).status_code == 400
    )
    # and no second check-in the same day, which would count the wage twice
    again = client.post("/attendance/me/check-in", headers=h, json=at())
    assert again.status_code == 400


def test_check_out_away_from_the_workshop_is_flagged(client, db, staff):
    make_unit(db, "MFG")
    _, h = staff()
    client.post("/attendance/me/check-in", headers=h, json=at())
    r = client.post("/attendance/me/check-out", headers=h, json=at(lat=NORTH))
    assert r.status_code == 200 and r.json()["check_out_location_status"] == "off_site"


def test_my_day_says_which_button_to_show(client, db, staff):
    make_unit(db, "MFG")
    _, h = staff()
    assert client.get("/attendance/me/today", headers=h).json() is None
    client.post("/attendance/me/check-in", headers=h, json=at())
    today = client.get("/attendance/me/today", headers=h).json()
    assert today["check_in"] and today["check_out"] is None


def test_a_row_a_manager_marked_has_no_start_time_so_it_cannot_be_checked_in_or_out(
    client, db, staff
):
    make_unit(db, "MFG")
    worker, h = staff()
    _, mh = staff("manager")
    marked = client.post(
        "/attendance/mark-present",
        headers=mh,
        json={"user_id": str(worker.id), **at()},
    )
    assert marked.status_code == 200, marked.text
    # already recorded today: the person cannot check in on top of it
    assert (
        client.post("/attendance/me/check-in", headers=h, json=at()).status_code == 400
    )
    assert (
        client.post("/attendance/me/check-out", headers=h, json=at()).status_code == 400
    )
    # the manager route says why instead of crashing
    r = client.post(
        f"/attendance/check-out?attendance_id={marked.json()['id']}", headers=mh
    )
    assert r.status_code == 400 and "no check-in time" in r.json()["detail"]


# ---------------------------------------------------------- who reports to whom
def set_boss(client, h, user, boss_id):
    return client.patch(
        f"/auth/users/{user.id}", headers=h, json={"reports_to_id": boss_id}
    )


def test_the_owner_sets_clears_and_cannot_loop_reporting_lines(client, staff):
    owner, oh = staff("owner")
    mgr, _ = staff("manager")
    lead, _ = staff("achari")
    worker, wh = staff("worker")

    assert set_boss(client, oh, mgr, str(owner.id)).json()["reports_to_id"] == str(
        owner.id
    )
    assert set_boss(client, oh, lead, str(mgr.id)).status_code == 200
    assert set_boss(client, oh, worker, str(lead.id)).status_code == 200

    # a loop, themselves, the owner under someone, someone switched off, an unknown id
    assert "loop" in set_boss(client, oh, mgr, str(worker.id)).json()["detail"]
    assert set_boss(client, oh, lead, str(lead.id)).status_code == 400
    assert set_boss(client, oh, owner, str(mgr.id)).status_code == 400
    gone, _ = staff("worker")
    client.patch(f"/auth/users/{gone.id}", headers=oh, json={"is_active": False})
    assert set_boss(client, oh, worker, str(gone.id)).status_code == 400
    assert set_boss(client, oh, worker, str(uuid.uuid4())).status_code == 400

    # only the owner decides it
    assert set_boss(client, wh, worker, None).status_code == 403
    # clear it, and leaving the field out keeps it
    assert set_boss(client, oh, worker, None).json()["reports_to_id"] is None
    client.patch(f"/auth/users/{lead.id}", headers=oh, json={"phone": "9"})
    assert client.get("/team/me", headers=oh).status_code == 200
    kept = [
        u for u in client.get("/auth/", headers=oh).json() if u["id"] == str(lead.id)
    ]
    assert kept[0]["reports_to_id"] == str(mgr.id)


def test_promoting_someone_to_owner_takes_them_to_the_top(client, staff):
    owner, oh = staff("owner")
    mgr, _ = staff("manager", boss=owner)
    r = client.patch(f"/auth/users/{mgr.id}", headers=oh, json={"role": "owner"})
    assert r.status_code == 200 and r.json()["reports_to_id"] is None


def test_the_chart_and_my_own_line(client, staff):
    owner, oh = staff("owner")
    mgr, mh = staff("manager", boss=owner)
    w1, w1h = staff("worker", boss=mgr)
    w2, _ = staff("worker", boss=mgr)

    tree = client.get("/team/tree", headers=w1h).json()  # any signed-in person may look
    mine = next(n for n in tree if n["id"] == str(owner.id))
    under_mgr = next(n for n in mine["reports"] if n["id"] == str(mgr.id))
    assert {n["id"] for n in under_mgr["reports"]} == {str(w1.id), str(w2.id)}

    me = client.get("/team/me", headers=w1h).json()
    assert me["manager"]["id"] == str(mgr.id) and me["reports"] == []
    boss = client.get("/team/me", headers=mh).json()
    assert {p["id"] for p in boss["reports"]} == {str(w1.id), str(w2.id)}


def test_a_switched_off_manager_does_not_hide_their_people(client, staff):
    owner, oh = staff("owner")
    mgr, _ = staff("manager", boss=owner)
    w, _ = staff("worker", boss=mgr)
    client.patch(f"/auth/users/{mgr.id}", headers=oh, json={"is_active": False})
    ids = {n["id"] for n in client.get("/team/tree", headers=oh).json()}
    assert str(w.id) in ids and str(mgr.id) not in ids


# ------------------------------------------------------- the manager's view
def test_a_manager_sees_their_people_the_owner_sees_everyone(client, db, staff):
    make_unit(db, "MFG")
    owner, oh = staff("owner")
    mgr, mh = staff("manager", boss=owner)
    in_w, in_h = staff("worker", boss=mgr)
    out_w, out_h = staff("worker", boss=mgr)
    idle, _ = staff("worker", boss=mgr)
    stranger, sh = staff("worker")  # reports to nobody

    client.post("/attendance/me/check-in", headers=in_h, json=at())
    client.post("/attendance/me/check-in", headers=out_h, json=at(lat=NORTH))
    client.post("/attendance/me/check-out", headers=out_h, json=at(lat=NORTH))
    client.post("/attendance/me/check-in", headers=sh, json=at())

    rows = {r["user_id"]: r for r in client.get("/team/attendance", headers=mh).json()}
    assert set(rows) == {str(in_w.id), str(out_w.id), str(idle.id)}  # not the stranger
    assert rows[str(in_w.id)]["state"] == "in" and not rows[str(in_w.id)]["off_site"]
    assert rows[str(out_w.id)]["state"] == "out" and rows[str(out_w.id)]["off_site"]
    assert (
        rows[str(idle.id)]["state"] == "not_in"
        and rows[str(idle.id)]["attendance"] is None
    )

    everyone = {r["user_id"] for r in client.get("/team/attendance", headers=oh).json()}
    assert {str(in_w.id), str(stranger.id), str(mgr.id)} <= everyone
    assert str(owner.id) not in everyone
    # nobody reports to a worker: they see nothing
    assert client.get("/team/attendance", headers=in_h).json() == []
    # another day is empty
    old = client.get("/team/attendance", headers=mh, params={"on": "2020-01-01"}).json()
    assert {r["state"] for r in old} == {"not_in"}


def test_a_manager_reaches_down_through_a_lead(client, db, staff):
    make_unit(db, "MFG")
    owner, _ = staff("owner")
    mgr, mh = staff("manager", boss=owner)
    lead, _ = staff("achari", boss=mgr)
    w, wh = staff("worker", boss=lead)
    client.post("/attendance/me/check-in", headers=wh, json=at())
    rows = client.get("/team/attendance", headers=mh).json()
    assert {r["user_id"] for r in rows} == {str(lead.id), str(w.id)}


# ------------------------------------------------------------- notifications
def test_the_manager_is_told_when_their_person_checks_in_away(client, db, staff):
    make_unit(db, "MFG")
    owner, oh = staff("owner")
    mgr, mh = staff("manager", boss=owner)
    w, wh = staff("worker", boss=mgr)
    other, ooh = staff("worker")
    since = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()

    client.post("/attendance/me/check-in", headers=wh, json=at(lat=NORTH))
    client.post("/attendance/me/check-in", headers=ooh, json=at(lat=NORTH))

    def kinds(h):
        got = client.get("/notifications", headers=h, params={"since": since}).json()
        return [i for i in got["items"] if i["kind"] == "attendance_off_site"]

    mine = kinds(mh)
    assert len(mine) == 1 and mine[0]["target_id"] == str(w.id)
    assert "away from the workshop" in mine[0]["title"]
    # the owner hears about everyone (the shared test database holds others' rows too)
    heard = {i["target_id"] for i in kinds(oh)}
    assert {str(w.id), str(other.id)} <= heard
    assert (
        kinds(wh) == []
    )  # the person themselves, and anyone without reports, hear nothing

    client.post("/attendance/me/check-out", headers=wh, json=at(lat=NORTH))
    ids = {i["id"] for i in kinds(mh)}
    assert len(ids) == 2 and any(i.endswith(":out") for i in ids)


def test_a_position_a_manager_sent_for_someone_is_not_that_persons_alarm(
    client, db, staff
):
    """mark-present stores the manager's position: off site there must not alert about the worker."""
    make_unit(db, "MFG")
    owner, _ = staff("owner")
    mgr, mh = staff("manager", boss=owner)
    w, _ = staff("worker", boss=mgr)
    since = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    r = client.post(
        "/attendance/mark-present",
        headers=mh,
        json={"user_id": str(w.id), **at(lat=NORTH)},
    )
    assert r.status_code == 200 and r.json()["location_status"] == "off_site"
    got = client.get("/notifications", headers=mh, params={"since": since}).json()
    assert [i for i in got["items"] if i["kind"] == "attendance_off_site"] == []
    rows = client.get("/team/attendance", headers=mh).json()
    assert next(x for x in rows if x["user_id"] == str(w.id))["off_site"] is False
