"""
Budgets for Machines and raw materials: the periods, what counts as spent, current value,
the 80% and 100% states, and who may set or see them.

The test database is shared by the whole run, so the spend tests use months in 1996 that
no other test touches, and call the service with that date as "today".
"""

import uuid
from datetime import date, datetime
from decimal import Decimal

import pytest

from app.models.purchase import Purchase
from app.models.purchase_item import PurchaseItem
from app.models.raw_material import RawMaterial
from app.services import budget_service as service


def machine_body(cost, bought, **over):
    return {
        "tag": f"BUD-{uuid.uuid4().hex[:8]}",
        "name": "Lathe",
        "purchase_date": bought,
        "purchase_cost": cost,
        **over,
    }


def add_machine(client, cost, bought):
    r = client.post("/machines/", json=machine_body(cost, bought))
    assert r.status_code == 201, r.text
    return r.json()


def add_upkeep(client, machine, on, cost, kind="repair"):
    r = client.post(
        f"/machines/{machine['id']}/maintenance",
        json={"kind": kind, "performed_on": on, "cost": cost},
    )
    assert r.status_code == 201, r.text


def set_budget(client, category, amount, on, period="month"):
    return client.put(
        "/budgets/",
        json={
            "category": category,
            "period": period,
            "starts_on": on,
            "amount": amount,
        },
    )


def purchase(db, material, line_total, when, status="confirmed"):
    p = Purchase(
        purchase_date=datetime.fromisoformat(when),
        total_amount=Decimal(line_total),
        status=status,
    )
    db.add(p)
    db.flush()
    db.add(
        PurchaseItem(
            purchase_id=p.id,
            raw_material_id=material.id,
            quantity=1,
            unit_price=Decimal(line_total),
            line_total=Decimal(line_total),
        )
    )
    db.commit()


def material(db, stock="0", price="0"):
    m = RawMaterial(
        name=f"Teak {uuid.uuid4().hex[:8]}",
        unit="cft",
        stock=Decimal(stock),
        current_price=Decimal(price),
    )
    db.add(m)
    db.commit()
    db.refresh(m)
    return m


# ----------------------------------------------------------------------- periods
@pytest.mark.parametrize(
    "period,on,start,end",
    [
        ("month", date(2026, 2, 15), date(2026, 2, 1), date(2026, 3, 1)),
        ("month", date(2026, 12, 31), date(2026, 12, 1), date(2027, 1, 1)),
        ("financial_year", date(2026, 3, 31), date(2025, 4, 1), date(2026, 4, 1)),
        ("financial_year", date(2026, 4, 1), date(2026, 4, 1), date(2027, 4, 1)),
        ("financial_year", date(2026, 1, 10), date(2025, 4, 1), date(2026, 4, 1)),
        ("financial_year", date(2026, 12, 25), date(2026, 4, 1), date(2027, 4, 1)),
    ],
)
def test_months_and_indian_financial_years(period, on, start, end):
    assert service.period_bounds(period, on) == (start, end)


# ------------------------------------------------------------------ who may do what
@pytest.mark.parametrize(
    "role,sees,sets",
    [
        ("owner", True, True),
        ("manager", True, False),
        ("sales", False, False),
        ("achari", False, False),
        ("worker", False, False),
    ],
)
def test_the_owner_sets_budgets_the_manager_reads_them(client_as, role, sees, sets):
    client, _ = client_as(role)
    for path in ("/budgets/current", "/budgets/warnings", "/budgets/"):
        assert (client.get(path).status_code == 200) is sees, path
    r = set_budget(client, "machines", "1000", "1996-08-10")
    assert (r.status_code == 200) is sets
    if not sets:
        assert r.status_code == 403
    assert client.delete(f"/budgets/{uuid.uuid4()}").status_code in (403, 404)


# --------------------------------------------------------------- setting a budget
def test_setting_a_budget_starts_on_the_first_and_setting_again_changes_it(client_as):
    owner, _ = client_as("owner")
    first = set_budget(owner, "machines", "50000", "1996-09-17")
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["period_start"] == "1996-09-01" and body["period_end"] == "1996-09-30"
    assert Decimal(body["amount"]) == 50000 and body["state"] == "ok"

    again = set_budget(owner, "machines", "65000", "1996-09-02")
    assert again.json()["id"] == body["id"] and Decimal(again.json()["amount"]) == 65000
    rows = owner.get("/budgets/?category=machines&period=month").json()
    assert [r["id"] for r in rows if r["period_start"] == "1996-09-01"] == [body["id"]]

    fy = set_budget(
        owner, "raw_materials", "900000", "1996-02-20", period="financial_year"
    )
    assert (
        fy.json()["period_start"] == "1995-04-01"
        and fy.json()["period_end"] == "1996-03-31"
    )


def test_bad_budgets_are_refused(client_as):
    owner, _ = client_as("owner")
    assert set_budget(owner, "machines", "-1", "1996-10-01").status_code == 422
    assert set_budget(owner, "wages", "100", "1996-10-01").status_code == 422
    bad_period = owner.put(
        "/budgets/",
        json={
            "category": "machines",
            "period": "week",
            "starts_on": "1996-10-01",
            "amount": 5,
        },
    )
    assert bad_period.status_code == 422
    assert owner.get("/budgets/?category=nope").status_code == 422


def test_a_budget_can_be_removed_without_touching_the_spending(client_as):
    owner, _ = client_as("owner")
    machine = add_machine(owner, "1000", "1996-11-05")
    made = set_budget(owner, "machines", "5000", "1996-11-01").json()
    assert owner.delete(f"/budgets/{made['id']}").status_code == 204
    assert owner.delete(f"/budgets/{made['id']}").status_code == 404
    assert owner.get(f"/machines/{machine['id']}").status_code == 200


# --------------------------------------------------------------- machines: spent
def test_machine_spend_counts_purchases_and_upkeep_in_the_period(client_as, db):
    owner, _ = client_as("owner")
    a = add_machine(owner, "30000", "1996-02-10")  # in February
    add_machine(owner, "99999", "1996-01-31")  # the day before
    add_upkeep(owner, a, "1996-02-20", "2000")
    add_upkeep(owner, a, "1996-03-01", "777")  # the day after
    cost_unknown = owner.post(
        "/machines/",
        json={
            "tag": f"BUD-{uuid.uuid4().hex[:8]}",
            "name": "Old drill",
            "purchase_date": "1996-02-11",
        },
    )
    assert cost_unknown.status_code == 201  # no cost recorded: counts as nothing

    owner_budget = set_budget(owner, "machines", "40000", "1996-02-01")
    assert owner_budget.status_code == 200
    s = owner_budget.json()
    assert Decimal(s["spent"]) == Decimal("32000.00")
    assert Decimal(s["remaining"]) == Decimal("8000.00")
    assert Decimal(s["percent_used"]) == Decimal("80.00") and s["state"] == "warning"

    add_upkeep(owner, a, "1996-02-25", "8000", kind="scheduled")
    over = service.current(db, date(1996, 2, 15))[0]
    assert over.category == "machines" and over.period == "month"
    assert over.spent == Decimal("40000.00") and over.remaining == Decimal("0.00")
    assert over.percent_used == Decimal("100.00") and over.state == "over"

    # the financial year April 1995 to March 1996 holds all of it
    fy = set_budget(owner, "machines", "200000", "1996-02-01", period="financial_year")
    assert Decimal(fy.json()["spent"]) == Decimal("30000") + 99999 + 2000 + 777 + 8000


# ----------------------------------------------------------- raw materials: spent
def test_raw_material_spend_is_confirmed_purchases_only(client_as, db):
    owner, _ = client_as("owner")
    teak = material(db)
    purchase(db, teak, "5000.00", "1996-05-12T10:00:00")
    purchase(db, teak, "250.50", "1996-05-31T23:30:00")  # last evening of the month
    purchase(db, teak, "7000.00", "1996-05-14T10:00:00", status="pending")
    purchase(db, teak, "9000.00", "1996-06-01T00:00:00")  # next month
    purchase(db, teak, "800.00", "1996-04-30T23:59:00")  # the month before

    s = set_budget(owner, "raw_materials", "10000", "1996-05-09").json()
    assert Decimal(s["spent"]) == Decimal("5250.50")
    assert Decimal(s["remaining"]) == Decimal("4749.50") and s["state"] == "ok"
    assert Decimal(s["percent_used"]) == Decimal("52.51")


def test_free_wood_adds_stock_but_spends_nothing(client_as, db):
    owner, _ = client_as("owner")
    teak = material(db, stock="0", price="0")
    owner.post(
        f"/raw-materials/{teak.id}/adjust",
        json={"quantity_change": "40", "notes": "reclaimed beams"},
    )
    before = Decimal(
        set_budget(owner, "raw_materials", "100", "1996-07-01").json()["spent"]
    )
    assert before == Decimal("0.00")  # nothing was bought


# ---------------------------------------------------------------- current value
def test_current_value_follows_the_machines_and_the_stock(client_as, db):
    owner, _ = client_as("owner")

    def values():
        by = {
            s["category"]: Decimal(s["current_value"])
            for s in owner.get("/budgets/current").json()
        }
        return by["machines"], by["raw_materials"]

    machines0, stock0 = values()
    add_machine(owner, "50000", "1996-03-03")  # no useful life: worth what it cost
    material(db, stock="10", price="100")
    machines1, stock1 = values()
    assert machines1 - machines0 == Decimal("50000.00")
    assert stock1 - stock0 == Decimal("1000.00")

    retired = add_machine(owner, "70000", "1996-03-04")
    owner.put(
        f"/machines/{retired['id']}",
        json={
            **machine_body("70000", "1996-03-04", tag=retired["tag"]),
            "status": "retired",
        },
    )
    assert values()[0] == machines1  # a retired machine is no longer an asset in use


# ------------------------------------------------------------------ the states
def test_states_with_no_budget_a_zero_budget_and_a_small_one(client_as, db):
    owner, _ = client_as("owner")
    none = service.current(db, date(1996, 12, 15))
    assert [(s.category, s.period, s.state) for s in none] == [
        ("machines", "month", "no_budget"),
        ("machines", "financial_year", "no_budget"),
        ("raw_materials", "month", "no_budget"),
        ("raw_materials", "financial_year", "no_budget"),
    ]
    assert all(
        s.amount is None and s.remaining is None and s.percent_used is None
        for s in none
    )

    a = add_machine(owner, "100", "1996-12-05")
    assert set_budget(owner, "machines", "0", "1996-12-01").json()["state"] == "over"
    assert set_budget(owner, "raw_materials", "0", "1996-12-01").json()["state"] == "ok"
    assert (
        set_budget(owner, "machines", "125", "1996-12-01").json()["state"] == "warning"
    )  # 80%
    assert (
        set_budget(owner, "machines", "126", "1996-12-01").json()["state"] == "ok"
    )  # 79.37%
    assert a["id"]


def test_warnings_lists_only_budgets_that_need_attention(client_as, db):
    owner, _ = client_as("owner")
    add_machine(owner, "900", "1996-08-05")
    set_budget(owner, "machines", "1000", "1996-08-01")  # 90%: warning
    set_budget(owner, "raw_materials", "1000", "1996-08-01")  # nothing spent: ok
    flagged = service.warnings(db, date(1996, 8, 15))
    assert [(w.category, w.period, w.state) for w in flagged] == [
        ("machines", "month", "warning")
    ]
    assert flagged[0].period_start == date(1996, 8, 1)
