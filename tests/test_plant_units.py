"""
Plant units and GPS-marked attendance: which unit a position falls in, what the
client may and may not decide, who may mark, and the offline sync path.
"""

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from app.models.attendance import Attendance
from app.models.daily_wage import DailyWage
from app.models.plant_unit import PlantUnit
from app.models.user import User
from app.services import plant_unit_service as geo

# Two points 111 m apart (0.001 degrees of latitude); all tests work around these.
LAT, LON = 12.9000, 77.5000
NORTH = 12.9010  # ~111 m north of LAT


@pytest.fixture(autouse=True)
def only_my_units(db):
    """The shared test DB keeps every unit ever made: switch them off first."""
    db.query(PlantUnit).update({PlantUnit.is_active: False})
    db.commit()


def make_unit(db, code, lat=LAT, lon=LON, radius=50, **kw):
    unit = PlantUnit(
        name=f"{code} {uuid.uuid4().hex[:6]}",
        code=f"{code}{uuid.uuid4().hex[:8]}".upper(),
        latitude=lat,
        longitude=lon,
        radius_m=radius,
        **kw,
    )
    db.add(unit)
    db.commit()
    db.refresh(unit)
    return unit


def make_staff(db, role="worker", wage="700.00"):
    uid = uuid.uuid4().hex[:8]
    user = User(
        username=f"staff_{uid}",
        email=f"staff_{uid}@example.com",
        password_hash="fakehash",
        role=role,
        is_active=True,
        base_daily_wage=Decimal(wage),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def mark(client, staff, lat=LAT, lon=LON, acc=10, **extra):
    body = {
        "user_id": str(staff.id),
        "latitude": lat,
        "longitude": lon,
        "accuracy_m": acc,
        **extra,
    }
    return client.post("/attendance/mark-present", json=body)


def row_of(db, staff):
    db.expire_all()
    return db.query(Attendance).filter(Attendance.user_id == staff.id).one()


# ----------------------------------------------------------- distance and checks
def test_distance_is_about_111_m_per_thousandth_of_a_degree():
    assert 110 < geo.distance_m(LAT, LON, NORTH, LON) < 112
    assert geo.distance_m(LAT, LON, LAT, LON) == 0


@pytest.mark.parametrize(
    "lat,lon,acc",
    [
        (91, 0, 5),
        (-90.1, 0, 5),
        (0, 181, 5),
        (0, -180.5, 5),
        (0, 0, -1),
        (0, 0, 100.1),  # vaguer than the 100 m default cap
        (float("nan"), 0, 5),
        (0, float("inf"), 5),
        (0, 0, float("nan")),
        ("north", 0, 5),
        (None, 0, 5),
    ],
)
def test_unusable_positions_are_refused(lat, lon, acc):
    with pytest.raises(geo.GeoError):
        geo.check_fix(lat, lon, acc)


def test_edge_values_are_accepted():
    assert geo.check_fix(90, 180, 0) == (90.0, 180.0, 0.0)
    assert geo.check_fix(-90, -180, 100) == (-90.0, -180.0, 100.0)


# ------------------------------------------------------------- the unit routes
def test_owner_creates_and_updates_units_staff_can_list(client_as, db):
    owner, _ = client_as("owner")
    body = {
        "name": f"Painting {uuid.uuid4().hex[:6]}",
        "code": "pnt" + uuid.uuid4().hex[:3],
        "latitude": LAT,
        "longitude": LON,
        "radius_m": 40,
    }
    made = owner.post("/plant-units/", json=body)
    assert made.status_code == 201, made.text
    unit = made.json()
    assert unit["code"] == body["code"].upper()  # codes are stored upper case
    assert owner.post("/plant-units/", json=body).status_code == 409

    changed = owner.put(f"/plant-units/{unit['id']}", json={**body, "radius_m": 60})
    assert changed.status_code == 200 and changed.json()["radius_m"] == 60
    off = owner.put(f"/plant-units/{unit['id']}", json={**body, "is_active": False})
    assert off.json()["is_active"] is False
    assert owner.put(f"/plant-units/{uuid.uuid4()}", json=body).status_code == 404

    worker, _ = client_as("worker")
    listed = worker.get("/plant-units/")
    assert listed.status_code == 200
    assert unit["id"] in [u["id"] for u in listed.json()]


@pytest.mark.parametrize("role", ["manager", "sales", "achari", "worker", "artisan"])
def test_only_the_owner_can_change_units(client_as, role):
    client, _ = client_as(role)
    body = {
        "name": "X",
        "code": "X",
        "latitude": LAT,
        "longitude": LON,
        "radius_m": 40,
    }
    assert client.post("/plant-units/", json=body).status_code == 403
    assert client.put(f"/plant-units/{uuid.uuid4()}", json=body).status_code == 403


@pytest.mark.parametrize(
    "change",
    [
        {"latitude": 91},
        {"longitude": -181},
        {"radius_m": 0},
        {"radius_m": -5},
        {"radius_m": 100000},
        {"code": " "},
        {"name": ""},
    ],
)
def test_a_unit_with_bad_numbers_is_refused(client_as, change):
    owner, _ = client_as("owner")
    body = {
        "name": "Bad",
        "code": "BAD",
        "latitude": LAT,
        "longitude": LON,
        "radius_m": 40,
        **change,
    }
    assert owner.post("/plant-units/", json=body).status_code == 422


# ----------------------------------------------------------------- unit matching
def test_a_position_inside_a_unit_is_matched_to_it(client_as, db):
    unit = make_unit(db, "MFG", radius=50)
    client, manager = client_as("manager")
    staff = make_staff(db)

    r = mark(client, staff, lat=LAT + 0.0002, acc=12)  # ~22 m from the centre
    assert r.status_code == 200, r.text
    row = row_of(db, staff)
    assert row.plant_unit_id == unit.id
    assert row.location_status == "on_site"
    assert (row.latitude, row.longitude, row.accuracy_m) == (LAT + 0.0002, LON, 12)
    assert row.recorded_by == manager.id
    assert row.status == "present"
    assert row.daily_wage == Decimal("700.00")
    assert row.wage_entry.amount == Decimal("700.00")
    assert row.wage_entry.payment_status == "pending"
    assert r.json()["plant_unit_id"] == str(unit.id)


def test_a_position_outside_every_unit_is_recorded_off_site_not_rejected(client_as, db):
    make_unit(db, "MFG", radius=50)
    client, _ = client_as("manager")
    staff = make_staff(db)

    r = mark(client, staff, lat=NORTH)  # ~111 m away, radius 50
    assert r.status_code == 200, r.text
    row = row_of(db, staff)
    assert row.plant_unit_id is None
    assert row.location_status == "off_site"
    assert row.wage_entry is not None  # still a normal pending wage

    owner, _ = client_as("owner")
    review = owner.get("/attendance/all?location_status=off_site&limit=100")
    assert str(row.id) in [a["id"] for a in review.json()]
    on_site = owner.get("/attendance/all?location_status=on_site&limit=100")
    assert str(row.id) not in [a["id"] for a in on_site.json()]


def test_with_no_units_at_all_the_mark_is_off_site(client_as, db):
    client, _ = client_as("manager")
    staff = make_staff(db)
    assert mark(client, staff).status_code == 200
    assert row_of(db, staff).location_status == "off_site"


def test_the_edge_of_the_circle_counts_as_inside(db):
    unit = make_unit(db, "EDGE", radius=geo.distance_m(LAT, LON, NORTH, LON))
    assert geo.find_unit(db, NORTH, LON).id == unit.id
    unit.radius_m -= 1
    db.commit()
    assert geo.find_unit(db, NORTH, LON) is None


def test_overlapping_units_go_to_the_nearest_centre(client_as, db):
    # B's centre is 111 m north of A's; both are wide enough to hold the middle
    a = make_unit(db, "A", lat=LAT, radius=100)
    b = make_unit(db, "B", lat=NORTH, radius=100)
    client, _ = client_as("manager")

    near_a, near_b = make_staff(db), make_staff(db)
    mark(client, near_a, lat=LAT + 0.0003)  # 33 m from A, 78 m from B
    mark(client, near_b, lat=LAT + 0.0007)  # 78 m from A, 33 m from B
    assert row_of(db, near_a).plant_unit_id == a.id
    assert row_of(db, near_b).plant_unit_id == b.id


def test_overlap_tie_goes_to_the_lower_code(db):
    b = make_unit(db, "B", lat=LAT, radius=100)
    a = make_unit(db, "A", lat=LAT, radius=100)  # same centre
    assert a.code < b.code
    assert geo.find_unit(db, LAT, LON).id == a.id


def test_a_switched_off_unit_is_ignored(client_as, db):
    make_unit(db, "OLD", radius=50, is_active=False)
    client, _ = client_as("manager")
    staff = make_staff(db)
    mark(client, staff)
    assert row_of(db, staff).plant_unit_id is None


# ----------------------------------------------------------- untrusted input
@pytest.mark.parametrize(
    "bad",
    [
        {"lat": 90.5},
        {"lat": -91},
        {"lon": 180.5},
        {"lon": -200},
        {"acc": -1},
        {"acc": 100.5},  # over the accuracy cap
        {"acc": 5000},
    ],
)
def test_invalid_coordinates_or_vague_accuracy_write_nothing(client_as, db, bad):
    make_unit(db, "MFG")
    client, _ = client_as("manager")
    staff = make_staff(db)
    r = mark(client, staff, **bad)
    assert r.status_code == 422, r.text
    assert db.query(Attendance).filter(Attendance.user_id == staff.id).count() == 0


def test_a_vague_fix_says_why(client_as, db):
    client, _ = client_as("manager")
    r = mark(client, make_staff(db), acc=250)
    assert r.status_code == 422 and "accuracy" in r.text.lower()


def test_missing_position_or_accuracy_is_refused(client_as, db):
    client, _ = client_as("manager")
    staff = make_staff(db)
    for drop in ("latitude", "longitude", "accuracy_m"):
        body = {
            "user_id": str(staff.id),
            "latitude": LAT,
            "longitude": LON,
            "accuracy_m": 5,
        }
        del body[drop]
        assert client.post("/attendance/mark-present", json=body).status_code == 422


def test_the_client_cannot_pick_the_unit_the_wage_or_the_pay_status(client_as, db):
    near = make_unit(db, "NEAR", radius=50)
    far = make_unit(db, "FAR", lat=LAT + 0.5, radius=50)
    client, _ = client_as("manager")
    staff = make_staff(db, wage="650.00")

    r = mark(
        client,
        staff,
        plant_unit_id=str(far.id),
        location_status="on_site",
        daily_wage=99999,
        payment_status="paid",
        date="2001-01-01",
        recorded_by=str(uuid.uuid4()),
    )
    assert r.status_code == 200, r.text
    row = row_of(db, staff)
    assert row.plant_unit_id == near.id  # the server's choice, not `far`
    assert row.daily_wage == Decimal("650.00")
    assert row.payment_status == "pending"
    assert row.wage_entry.payment_status == "pending"
    assert row.date == datetime.now(timezone.utc).date()


def test_half_day_pays_half_and_absent_is_not_allowed(client_as, db):
    client, _ = client_as("manager")
    staff = make_staff(db, wage="700.00")
    assert mark(client, staff, status="half-day").status_code == 200
    assert row_of(db, staff).wage_entry.amount == Decimal("350.00")
    assert mark(client, make_staff(db), status="absent").status_code == 422


# ----------------------------------------------------------------------- rules
@pytest.mark.parametrize(
    "role,expected",
    [
        ("owner", 200),
        ("manager", 200),
        ("sales", 403),
        ("achari", 403),
        ("worker", 403),
        ("artisan", 403),
    ],
)
def test_who_may_mark_by_gps(client_as, db, role, expected):
    client, _ = client_as(role)
    staff = make_staff(db)
    assert mark(client, staff).status_code == expected
    count = db.query(Attendance).filter(Attendance.user_id == staff.id).count()
    assert count == (1 if expected == 200 else 0)


def test_a_manager_cannot_mark_themselves_but_the_owner_can(client_as, db):
    manager, boss = client_as("manager")
    assert mark(manager, boss).status_code == 403
    owner, boss = client_as("owner")
    assert mark(owner, boss).status_code == 200


def test_unknown_or_switched_off_staff_are_not_found(client_as, db):
    client, _ = client_as("manager")
    ghost = User(id=uuid.uuid4())
    assert mark(client, ghost).status_code == 404
    gone = make_staff(db)
    gone.is_active = False
    db.commit()
    assert mark(client, gone).status_code == 404


def test_one_mark_a_day_and_a_replayed_client_id_is_the_same_row(client_as, db):
    client, _ = client_as("manager")
    staff = make_staff(db)
    cid = str(uuid.uuid4())
    first = mark(client, staff, client_id=cid)
    again = mark(client, staff, client_id=cid)
    assert first.status_code == again.status_code == 200
    assert first.json()["id"] == again.json()["id"]
    second = mark(client, staff)  # a new request the same day
    assert second.status_code == 400
    assert db.query(Attendance).filter(Attendance.user_id == staff.id).count() == 1


def test_the_old_attendance_route_still_works_without_a_position(client_as, db):
    client, _ = client_as("sales")
    staff = make_staff(db)
    r = client.post(
        "/attendance/",
        json={
            "user_id": str(staff.id),
            "date": date.today().isoformat(),
            "status": "present",
            "daily_wage": 500,
        },
    )
    assert r.status_code == 200, r.text
    row = row_of(db, staff)
    assert row.location_status is None and row.plant_unit_id is None


# ------------------------------------------------------------------ offline sync
def push(client, *ops):
    r = client.post("/sync/push", json={"operations": list(ops)})
    assert r.status_code == 200, r.text
    return r.json()


def att_op(staff, action="create", att_id=None, **payload):
    att_id = att_id or str(uuid.uuid4())
    op = {
        "id": str(uuid.uuid4()),
        "entity": "attendance",
        "action": action,
        "payload": {
            "id": att_id,
            "user_id": str(staff.id),
            "date": date.today().isoformat(),
            "status": "present",
            "daily_wage": 1,
            **payload,
        },
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    return op, att_id


def test_sync_carries_the_position_and_the_server_decides_the_rest(client_as, db):
    unit = make_unit(db, "MFG", radius=50)
    far = make_unit(db, "FAR", lat=LAT + 0.5, radius=50)
    client, manager = client_as("manager")
    staff = make_staff(db, wage="640.00")

    op, att_id = att_op(
        staff,
        latitude=LAT,
        longitude=LON,
        accuracy_m=8,
        daily_wage=99999,
        payment_status="paid",
        plant_unit_id=str(far.id),
        location_status="on_site",
    )
    out = push(client, op)
    assert out["failed"] == [], out

    db.expire_all()
    row = db.get(Attendance, uuid.UUID(att_id))
    assert row.user_id == staff.id  # about the person marked, not the manager
    assert row.recorded_by == manager.id
    assert row.plant_unit_id == unit.id
    assert row.location_status == "on_site"
    assert (row.latitude, row.longitude, row.accuracy_m) == (LAT, LON, 8)
    assert row.daily_wage == Decimal("640.00")
    assert row.payment_status == "pending"
    assert row.wage_entry.amount == Decimal("640.00")


def test_sync_replay_makes_one_row_and_one_wage(client_as, db):
    make_unit(db, "MFG")
    client, _ = client_as("manager")
    staff = make_staff(db)
    op, att_id = att_op(staff, latitude=LAT, longitude=LON, accuracy_m=5)
    again = {**op, "id": str(uuid.uuid4())}
    assert push(client, op)["failed"] == []
    assert push(client, again)["failed"] == []
    assert push(client, op)["failed"] == []
    db.expire_all()
    assert db.query(Attendance).filter(Attendance.user_id == staff.id).count() == 1
    wages = db.query(DailyWage).filter(DailyWage.attendance_id == uuid.UUID(att_id))
    assert wages.count() == 1


def test_sync_off_site_is_recorded_like_the_rest_call(client_as, db):
    make_unit(db, "MFG", radius=20)
    client, _ = client_as("manager")
    staff = make_staff(db)
    op, att_id = att_op(staff, latitude=NORTH, longitude=LON, accuracy_m=5)
    assert push(client, op)["failed"] == []
    row = db.get(Attendance, uuid.UUID(att_id))
    assert row.plant_unit_id is None and row.location_status == "off_site"


@pytest.mark.parametrize(
    "geo_fields",
    [
        {"latitude": 95, "longitude": LON, "accuracy_m": 5},
        {"latitude": LAT, "longitude": 181, "accuracy_m": 5},
        {"latitude": LAT, "longitude": LON, "accuracy_m": -3},
        {"latitude": LAT, "longitude": LON, "accuracy_m": 400},
        {"latitude": LAT, "longitude": LON},  # no accuracy
        {"latitude": "north", "longitude": LON, "accuracy_m": 5},
        {"latitude": LAT},
    ],
)
def test_sync_refuses_a_bad_position_for_that_operation_only(client_as, db, geo_fields):
    client, _ = client_as("manager")
    bad_staff, good_staff = make_staff(db), make_staff(db)
    bad, bad_id = att_op(bad_staff, **geo_fields)
    good, good_id = att_op(good_staff, latitude=LAT, longitude=LON, accuracy_m=5)
    out = push(client, bad, good)
    assert [f["client_id"] for f in out["failed"]] == [bad["id"]]
    assert [s["client_id"] for s in out["success"]] == [good["id"]]
    db.expire_all()
    assert db.get(Attendance, uuid.UUID(bad_id)) is None
    assert db.get(Attendance, uuid.UUID(good_id)) is not None


def test_sync_sales_cannot_mark_by_gps_but_plain_attendance_still_syncs(client_as, db):
    client, sales = client_as("sales")
    staff = make_staff(db)
    op, att_id = att_op(staff, latitude=LAT, longitude=LON, accuracy_m=5)
    out = push(client, op)
    assert out["success"] == [] and "gps" in out["failed"][0]["error"].lower()
    assert db.get(Attendance, uuid.UUID(att_id)) is None

    plain, plain_id = att_op(sales)  # as before: no position, own wage
    assert push(client, plain)["failed"] == []
    assert db.get(Attendance, uuid.UUID(plain_id)).location_status is None


def test_sync_ignores_a_unit_or_status_sent_without_a_position(client_as, db):
    unit = make_unit(db, "MFG")
    client, manager = client_as("manager")
    op, att_id = att_op(manager, plant_unit_id=str(unit.id), location_status="on_site")
    assert push(client, op)["failed"] == []
    row = db.get(Attendance, uuid.UUID(att_id))
    assert row.plant_unit_id is None and row.location_status is None


def test_sync_rules_match_the_rest_call(client_as, db):
    make_unit(db, "MFG")
    client, boss = client_as("manager")
    fix = {"latitude": LAT, "longitude": LON, "accuracy_m": 5}

    own, own_id = att_op(boss, **fix)  # own attendance
    gone = make_staff(db)
    gone.is_active = False
    db.commit()
    off, off_id = att_op(gone, **fix)  # switched-off account
    absent, absent_id = att_op(make_staff(db), status="absent", **fix)
    staff = make_staff(db)
    first, first_id = att_op(staff, **fix)
    second, second_id = att_op(staff, **fix)  # same person, same day

    out = push(client, own, off, absent, first, second)
    assert {f["client_id"] for f in out["failed"]} == {
        own["id"],
        off["id"],
        absent["id"],
        second["id"],
    }
    assert [s["client_id"] for s in out["success"]] == [first["id"]]
    for missing in (own_id, off_id, absent_id, second_id):
        assert db.get(Attendance, uuid.UUID(missing)) is None


def test_a_later_sync_update_cannot_move_the_position_unit_or_wage(client_as, db):
    unit = make_unit(db, "MFG")
    far = make_unit(db, "FAR", lat=LAT + 0.5)
    client, _ = client_as("manager")
    staff = make_staff(db, wage="600.00")
    op, att_id = att_op(staff, latitude=LAT, longitude=LON, accuracy_m=5)
    assert push(client, op)["failed"] == []

    upd, _ = att_op(
        staff,
        action="update",
        att_id=att_id,
        status="half-day",
        latitude=LAT + 0.5,
        longitude=LON,
        accuracy_m=1,
        plant_unit_id=str(far.id),
        location_status="off_site",
        daily_wage=99999,
        payment_status="paid",
    )
    assert push(client, upd)["failed"] == []
    db.expire_all()
    row = db.get(Attendance, uuid.UUID(att_id))
    assert row.status == "half-day"  # ordinary fields still sync
    assert row.plant_unit_id == unit.id and row.location_status == "on_site"
    assert (row.latitude, row.longitude, row.accuracy_m) == (LAT, LON, 5)
    assert row.daily_wage == Decimal("600.00") and row.payment_status == "pending"


def test_pull_sends_the_location_columns(client_as, db):
    unit = make_unit(db, "MFG")
    client, _ = client_as("manager")
    staff = make_staff(db)
    op, att_id = att_op(staff, latitude=LAT, longitude=LON, accuracy_m=5)
    # a far-future stamp, so the first page of a pull holds only this row
    op["updated_at"] = "2097-06-01T12:00:00+00:00"
    assert push(client, op)["failed"] == []
    pulled = client.get("/sync/pull", params={"last_sync": "2097-06-01T11:59:00Z"})
    assert pulled.status_code == 200, pulled.text
    row = next(a for a in pulled.json()["attendance"] if a["id"] == att_id)
    assert row["plant_unit_id"] == str(unit.id) and row["location_status"] == "on_site"
