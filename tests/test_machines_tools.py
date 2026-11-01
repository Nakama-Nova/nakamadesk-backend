"""
The machine register (cost, worth now, service due, maintenance, downtime) and the tool
register (who has which tool, what state it came back in).
"""

import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.models.machine import Machine
from app.models.plant_unit import PlantUnit
from app.models.user import User
from app.services import machine_service

TODAY = date.today()


def unique(prefix):
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def machine_body(**over):
    return {
        "tag": unique("MCH"),
        "name": "Band saw",
        "make_model": "Acme BS-300",
        "purchase_date": (TODAY - timedelta(days=400)).isoformat(),
        "purchase_cost": "120000.00",
        "useful_life_years": 10,
        "salvage_value": "12000.00",
        **over,
    }


def make_machine(client, **over):
    r = client.post("/machines/", json=machine_body(**over))
    assert r.status_code == 201, r.text
    return r.json()


def make_location(db, name="Machinery"):
    uid = uuid.uuid4().hex[:6]
    unit = PlantUnit(
        name=f"{name} {uid}",
        code=f"MC{uid}".upper(),
        latitude=12.9,
        longitude=77.5,
        radius_m=50,
    )
    db.add(unit)
    db.commit()
    db.refresh(unit)
    return unit


def make_user(db, role="worker", active=True):
    uid = uuid.uuid4().hex[:8]
    user = User(
        username=f"{role}_{uid}",
        email=f"{role}_{uid}@example.com",
        password_hash="x",
        role=role,
        is_active=active,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


# ------------------------------------------------------------------ depreciation
def machine(**kw):
    base = dict(
        purchase_cost=Decimal("100000"),
        salvage_value=Decimal("10000"),
        useful_life_years=5,
        purchase_date=date(2024, 1, 15),
    )
    return Machine(**{**base, **kw})


def test_worth_now_is_written_off_in_a_straight_line_by_whole_months():
    # (100000 - 10000) / 5 = 18000 a year = 1500 a month
    written, worth = machine_service.depreciation(machine(), date(2026, 1, 15))
    assert (written, worth) == (Decimal("36000.00"), Decimal("64000.00"))  # 24 months
    written, worth = machine_service.depreciation(machine(), date(2026, 1, 14))
    assert (written, worth) == (Decimal("34500.00"), Decimal("65500.00"))  # 23 months
    assert machine_service.depreciation(machine(), date(2024, 1, 15))[0] == Decimal(
        "0.00"
    )


def test_it_never_goes_below_the_salvage_value():
    written, worth = machine_service.depreciation(machine(), date(2040, 1, 1))
    assert (written, worth) == (Decimal("90000.00"), Decimal("10000.00"))
    odd = machine(salvage_value=Decimal("500000"))  # more than it cost
    assert machine_service.depreciation(odd, date(2030, 1, 1))[1] == Decimal(
        "100000.00"
    )


def test_without_a_life_or_a_date_it_is_not_written_off_and_without_a_cost_it_is_unknown():
    assert machine_service.depreciation(machine(useful_life_years=None))[1] == Decimal(
        "100000.00"
    )
    assert machine_service.depreciation(machine(purchase_date=None))[1] == Decimal(
        "100000.00"
    )
    assert machine_service.depreciation(machine(purchase_cost=None)) == (None, None)


# ---------------------------------------------------------------------- register
@pytest.mark.parametrize(
    "role,ok",
    [
        ("owner", True),
        ("manager", True),
        ("sales", False),
        ("achari", False),
        ("worker", False),
        ("artisan", False),
    ],
)
def test_only_owner_and_manager_use_the_machine_register(client_as, role, ok):
    client, _ = client_as(role)
    assert (client.post("/machines/", json=machine_body()).status_code == 201) is ok
    assert (client.get("/machines/").status_code == 200) is ok
    assert (client.get("/machines/due").status_code == 200) is ok


def test_a_machine_shows_its_worth_service_date_and_running_cost(client_as, db):
    owner, _ = client_as("owner")
    place = make_location(db)
    body = machine_body(
        location_id=str(place.id),
        purchase_date=(TODAY - timedelta(days=730)).isoformat(),
        maintenance_every_days=90,
    )
    made = owner.post("/machines/", json=body)
    assert made.status_code == 201, made.text
    m = made.json()
    assert m["location_id"] == str(place.id) and m["status"] == "active"
    assert Decimal(m["book_value"]) < Decimal("120000") and Decimal(
        m["book_value"]
    ) > Decimal("12000")
    assert Decimal(m["accumulated_depreciation"]) + Decimal(m["book_value"]) == Decimal(
        "120000.00"
    )
    assert m["service_overdue"] is True  # bought two years ago, never serviced
    assert Decimal(m["maintenance_spent"]) == 0
    assert owner.get(f"/machines/{m['id']}").json()["tag"] == body["tag"]
    assert owner.get(f"/machines/{uuid.uuid4()}").status_code == 404


def test_bad_machines_are_refused(client_as, db):
    owner, _ = client_as("owner")
    first = make_machine(owner)
    assert (
        owner.post("/machines/", json=machine_body(tag=first["tag"])).status_code == 409
    )
    ghost = owner.post("/machines/", json=machine_body(location_id=str(uuid.uuid4())))
    assert ghost.status_code == 400
    off = make_location(db)
    off.is_active = False
    db.commit()
    assert (
        owner.post("/machines/", json=machine_body(location_id=str(off.id))).status_code
        == 400
    )
    for bad in (
        {"purchase_cost": "-5"},
        {"useful_life_years": 0},
        {"tag": "  "},
        {"name": ""},
        {"maintenance_every_days": 0},
        {"salvage_value": "-1"},
    ):
        assert (
            owner.post("/machines/", json=machine_body(**bad)).status_code == 422
        ), bad


def test_changing_a_machine_and_retiring_it(client_as):
    owner, _ = client_as("owner")
    m = make_machine(owner)
    body = {**machine_body(tag=m["tag"]), "name": "Band saw (big)", "status": "retired"}
    r = owner.put(f"/machines/{m['id']}", json=body)
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "Band saw (big)" and r.json()["status"] == "retired"
    assert (
        owner.put(f"/machines/{m['id']}", json={**body, "status": "down"}).status_code
        == 422
    )
    other = make_machine(owner)
    clash = owner.put(f"/machines/{other['id']}", json=machine_body(tag=m["tag"]))
    assert clash.status_code == 409


def test_the_list_can_be_filtered(client_as, db):
    owner, _ = client_as("owner")
    place = make_location(db)
    mine = make_machine(owner, location_id=str(place.id))
    make_machine(owner)
    rows = owner.get(f"/machines/?location_id={place.id}").json()
    assert [r["id"] for r in rows] == [mine["id"]]
    assert owner.get("/machines/?status=nope").status_code == 422


# ------------------------------------------------------------------- maintenance
def test_a_scheduled_service_moves_the_due_date_a_repair_does_not(client_as):
    owner, _ = client_as("owner")
    m = make_machine(
        owner,
        purchase_date=(TODAY - timedelta(days=100)).isoformat(),
        maintenance_every_days=30,
    )
    assert m["service_due_on"] == (TODAY - timedelta(days=70)).isoformat()
    assert m["service_overdue"] is True
    assert m["id"] in [x["id"] for x in owner.get("/machines/due?within_days=0").json()]

    repair = owner.post(
        f"/machines/{m['id']}/maintenance",
        json={
            "kind": "repair",
            "performed_on": TODAY.isoformat(),
            "cost": "2500",
            "description": "belt",
        },
    )
    assert repair.status_code == 201, repair.text
    after_repair = owner.get(f"/machines/{m['id']}").json()
    assert after_repair["service_overdue"] is True  # a repair is not a service
    assert Decimal(after_repair["maintenance_spent"]) == Decimal("2500.00")

    service = owner.post(
        f"/machines/{m['id']}/maintenance",
        json={
            "kind": "scheduled",
            "performed_on": TODAY.isoformat(),
            "cost": "800",
            "done_by": "Ravi",
        },
    )
    assert service.status_code == 201
    done = owner.get(f"/machines/{m['id']}").json()
    assert done["service_due_on"] == (TODAY + timedelta(days=30)).isoformat()
    assert done["service_overdue"] is False and Decimal(
        done["maintenance_spent"]
    ) == Decimal("3300.00")
    assert m["id"] not in [
        x["id"] for x in owner.get("/machines/due?within_days=0").json()
    ]
    assert m["id"] in [
        x["id"] for x in owner.get("/machines/due?within_days=30").json()
    ]

    history = owner.get(f"/machines/{m['id']}/maintenance").json()
    assert len(history) == 2 and {h["kind"] for h in history} == {"scheduled", "repair"}


def test_maintenance_must_have_happened_and_cost_something_sensible(client_as):
    owner, _ = client_as("owner")
    m = make_machine(owner)
    url = f"/machines/{m['id']}/maintenance"
    tomorrow = (TODAY + timedelta(days=1)).isoformat()
    assert (
        owner.post(url, json={"kind": "repair", "performed_on": tomorrow}).status_code
        == 400
    )
    ok = {"kind": "repair", "performed_on": TODAY.isoformat()}
    assert owner.post(url, json={**ok, "cost": "-1"}).status_code == 422
    assert owner.post(url, json={**ok, "kind": "paint"}).status_code == 422
    assert (
        owner.post(f"/machines/{uuid.uuid4()}/maintenance", json=ok).status_code == 404
    )


def test_the_due_list_leaves_out_retired_machines_and_ones_with_no_schedule(client_as):
    owner, _ = client_as("owner")
    old = (TODAY - timedelta(days=500)).isoformat()
    scheduled = make_machine(owner, purchase_date=old, maintenance_every_days=30)
    retired = make_machine(owner, purchase_date=old, maintenance_every_days=30)
    owner.put(
        f"/machines/{retired['id']}",
        json={
            **machine_body(
                tag=retired["tag"], purchase_date=old, maintenance_every_days=30
            ),
            "status": "retired",
        },
    )
    unscheduled = make_machine(owner, purchase_date=old)
    due = [x["id"] for x in owner.get("/machines/due?within_days=0").json()]
    assert (
        scheduled["id"] in due
        and retired["id"] not in due
        and unscheduled["id"] not in due
    )
    assert owner.get(f"/machines/{retired['id']}").json()["service_overdue"] is False


# ---------------------------------------------------------------------- downtime
def test_downtime_marks_the_machine_down_until_it_is_ended(client_as):
    owner, _ = client_as("owner")
    m = make_machine(owner)
    url = f"/machines/{m['id']}/downtime"
    started = owner.post(url, json={"reason": "motor burnt"})
    assert started.status_code == 201 and started.json()["ended_at"] is None
    assert owner.get(f"/machines/{m['id']}").json()["status"] == "down"
    assert owner.post(url, json={}).status_code == 409  # already down
    stuck = owner.put(
        f"/machines/{m['id']}", json={**machine_body(tag=m["tag"]), "status": "retired"}
    )
    assert stuck.status_code == 409  # end the downtime first

    ended = owner.post(f"{url}/end")
    assert ended.status_code == 200 and ended.json()["ended_at"] is not None
    assert owner.get(f"/machines/{m['id']}").json()["status"] == "active"
    assert owner.post(f"{url}/end").status_code == 409  # not down any more
    log = owner.get(url).json()
    assert len(log) == 1 and log[0]["reason"] == "motor burnt"


def test_a_retired_machine_takes_no_more_work(client_as):
    owner, _ = client_as("owner")
    m = make_machine(owner)
    owner.put(
        f"/machines/{m['id']}", json={**machine_body(tag=m["tag"]), "status": "retired"}
    )
    assert owner.post(f"/machines/{m['id']}/downtime", json={}).status_code == 409
    done = {"kind": "repair", "performed_on": TODAY.isoformat()}
    assert owner.post(f"/machines/{m['id']}/maintenance", json=done).status_code == 409
    back = owner.put(
        f"/machines/{m['id']}", json={**machine_body(tag=m["tag"]), "status": "active"}
    )
    assert back.status_code == 200 and back.json()["status"] == "active"


# ------------------------------------------------------------------------- tools
def tool_body(**over):
    return {
        "tag": unique("TL"),
        "name": "Carving chisel set",
        "category": "carving",
        **over,
    }


def make_tool(client, **over):
    r = client.post("/tools/", json=tool_body(**over))
    assert r.status_code == 201, r.text
    return r.json()


@pytest.mark.parametrize(
    "role,manage,view",
    [
        ("owner", True, True),
        ("manager", True, True),
        ("achari", False, True),
        ("sales", False, False),
        ("worker", False, False),
        ("artisan", False, False),
    ],
)
def test_who_manages_and_who_looks_at_tools(client_as, db, role, manage, view):
    client, _ = client_as(role)
    assert (client.post("/tools/", json=tool_body()).status_code == 201) is manage
    assert (client.get("/tools/").status_code == 200) is view
    assert client.get("/tools/mine").status_code == 200  # any signed-in staff
    someone = make_user(db)
    issue = client.post(
        f"/tools/{uuid.uuid4()}/issue", json={"user_id": str(someone.id)}
    )
    assert issue.status_code == (404 if manage else 403)  # 404: allowed, no such tool


def test_a_tool_is_added_changed_and_found_by_status(client_as, db):
    owner, _ = client_as("owner")
    place = make_location(db)
    t = make_tool(
        owner, location_id=str(place.id), purchase_cost="1800.50", condition="new"
    )
    assert t["status"] == "available" and t["condition"] == "new"
    assert owner.post("/tools/", json=tool_body(tag=t["tag"])).status_code == 409
    assert (
        owner.post("/tools/", json=tool_body(location_id=str(uuid.uuid4()))).status_code
        == 400
    )
    assert owner.post("/tools/", json=tool_body(condition="broken")).status_code == 422
    assert owner.post("/tools/", json=tool_body(purchase_cost="-1")).status_code == 422

    changed = owner.put(
        f"/tools/{t['id']}",
        json={
            **tool_body(tag=t["tag"], location_id=str(place.id)),
            "name": "Chisel set B",
        },
    )
    assert changed.status_code == 200 and changed.json()["name"] == "Chisel set B"
    assert [
        x["id"]
        for x in owner.get(
            "/tools/?status=available&location_id=" + str(place.id)
        ).json()
    ] == [t["id"]]
    assert owner.get("/tools/?status=nope").status_code == 422
    assert owner.get(f"/tools/{uuid.uuid4()}").status_code == 404


def test_issuing_and_returning_a_tool(client_as, db):
    owner, boss = client_as("owner")
    t = make_tool(owner)
    carver = make_user(db, "artisan")

    issued = owner.post(f"/tools/{t['id']}/issue", json={"user_id": str(carver.id)})
    assert issued.status_code == 201, issued.text
    assert (
        issued.json()["user_name"] == carver.username
        and issued.json()["condition_out"] == "good"
    )
    now = owner.get(f"/tools/{t['id']}").json()
    assert now["status"] == "issued" and now["held_by"] == str(carver.id)

    other = make_user(db)
    taken = owner.post(f"/tools/{t['id']}/issue", json={"user_id": str(other.id)})
    assert taken.status_code == 409 and carver.username in taken.json()["detail"]
    stuck = owner.put(
        f"/tools/{t['id']}", json={**tool_body(tag=t["tag"]), "status": "retired"}
    )
    assert stuck.status_code == 409  # return it first

    back = owner.post(
        f"/tools/{t['id']}/return", json={"outcome": "ok", "condition_in": "worn"}
    )
    assert (
        back.status_code == 200
        and back.json()["returned_at"]
        and back.json()["condition_in"] == "worn"
    )
    after = owner.get(f"/tools/{t['id']}").json()
    assert (
        after["status"] == "available"
        and after["condition"] == "worn"
        and after["held_by"] is None
    )
    assert (
        owner.post(f"/tools/{t['id']}/return", json={}).status_code == 409
    )  # not issued

    again = owner.post(f"/tools/{t['id']}/issue", json={"user_id": str(other.id)})
    assert again.status_code == 201 and again.json()["condition_out"] == "worn"
    history = owner.get(f"/tools/{t['id']}/history").json()
    assert [h["user_name"] for h in history] == [other.username, carver.username]


def test_damaged_and_lost_tools_leave_the_pool_until_dealt_with(client_as, db):
    owner, _ = client_as("owner")
    worker = make_user(db)
    broken = make_tool(owner)
    owner.post(f"/tools/{broken['id']}/issue", json={"user_id": str(worker.id)})
    r = owner.post(
        f"/tools/{broken['id']}/return", json={"outcome": "damaged", "notes": "snapped"}
    )
    assert r.status_code == 200 and r.json()["outcome"] == "damaged"
    state = owner.get(f"/tools/{broken['id']}").json()
    assert state["status"] == "broken" and state["condition"] == "damaged"
    refused = owner.post(
        f"/tools/{broken['id']}/issue", json={"user_id": str(worker.id)}
    )
    assert refused.status_code == 409 and "broken" in refused.json()["detail"]
    fixed = owner.put(
        f"/tools/{broken['id']}",
        json={**tool_body(tag=broken["tag"], condition="good"), "status": "available"},
    )
    assert fixed.status_code == 200 and fixed.json()["status"] == "available"

    lost = make_tool(owner)
    owner.post(f"/tools/{lost['id']}/issue", json={"user_id": str(worker.id)})
    owner.post(f"/tools/{lost['id']}/return", json={"outcome": "lost"})
    assert owner.get(f"/tools/{lost['id']}").json()["status"] == "lost"
    assert (
        owner.post(
            f"/tools/{lost['id']}/issue", json={"user_id": str(worker.id)}
        ).status_code
        == 409
    )


def test_a_tool_can_only_go_to_a_real_active_person(client_as, db):
    owner, _ = client_as("owner")
    t = make_tool(owner)
    assert (
        owner.post(
            f"/tools/{t['id']}/issue", json={"user_id": str(uuid.uuid4())}
        ).status_code
        == 404
    )
    gone = make_user(db, active=False)
    assert (
        owner.post(
            f"/tools/{t['id']}/issue", json={"user_id": str(gone.id)}
        ).status_code
        == 404
    )
    assert owner.get(f"/tools/{t['id']}").json()["status"] == "available"


def test_a_person_sees_the_tools_they_hold(client_as, db):
    from app.db.deps import get_current_user
    from app.main import app

    owner, _ = client_as("owner")
    held, handed_back, not_theirs = make_tool(owner), make_tool(owner), make_tool(owner)
    worker, other = make_user(db), make_user(db, "artisan")
    owner.post(f"/tools/{held['id']}/issue", json={"user_id": str(worker.id)})
    owner.post(f"/tools/{handed_back['id']}/issue", json={"user_id": str(worker.id)})
    owner.post(f"/tools/{handed_back['id']}/return", json={})
    owner.post(f"/tools/{not_theirs['id']}/issue", json={"user_id": str(other.id)})

    app.dependency_overrides[get_current_user] = lambda: worker  # sign in as the worker
    mine = owner.get("/tools/mine").json()
    assert [m["tool_id"] for m in mine] == [held["id"]] and mine[0][
        "returned_at"
    ] is None
    assert (
        owner.get("/tools/").status_code == 403
    )  # a worker may not browse the register
