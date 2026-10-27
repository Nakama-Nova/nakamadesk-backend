"""
Expenses, payroll (daily wage + per-piece + overtime + advances) and the real
profit report. Money tests use their own far-past dates so rows written by other
tests never change the totals asserted here.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal

import pytest

from app.models.attendance import Attendance
from app.models.daily_wage import DailyWage
from app.models.purchase import Purchase
from app.models.sale import Sale
from app.models.user import User


def D(x):
    return Decimal(str(x))


@pytest.fixture
def staff(db):
    """A worker on Rs 800/day, Rs 100/hour overtime."""
    u = User(
        username=f"w_{uuid.uuid4().hex[:6]}",
        email=f"w_{uuid.uuid4().hex[:6]}@example.com",
        password_hash="x",
        role="worker",
        is_active=True,
        base_daily_wage=D(800),
        overtime_rate_per_hour=D(100),
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def work_day(db, user, day, status="present", wage=800):
    """One attendance day with its pending wage, as the attendance flow makes it."""
    att = Attendance(
        user_id=user.id,
        date=day,
        status=status,
        daily_wage=D(wage),
        recorded_by=user.id,
    )
    db.add(att)
    db.flush()
    amount = {"present": D(wage), "half-day": D(wage) / 2, "absent": D(0)}[status]
    db.add(DailyWage(attendance_id=att.id, amount=amount, payment_status="pending"))
    db.commit()


# ---------------------------------------------------------------- expenses


def test_expense_lifecycle_and_summary(client, people):
    owner, manager = people["owner"]["h"], people["manager"]["h"]
    body = {"spent_on": "2001-03-05", "category": " shop rent ", "amount": "6000.50"}
    made = client.post("/expenses/", headers=manager, json=body)
    assert made.status_code == 201, made.text
    e = made.json()
    assert e["category"] == "Shop Rent"  # tidied so reports group together
    client.post(
        "/expenses/",
        headers=owner,
        json={**body, "category": "Electricity", "amount": 900},
    )

    summary = client.get(
        "/expenses/summary?start_date=2001-03-01&end_date=2001-03-31", headers=owner
    ).json()
    assert D(summary["total"]) == D("6900.50")
    assert summary["by_category"][0]["category"] == "Shop Rent"

    edit = client.patch(f"/expenses/{e['id']}", headers=manager, json={"amount": 6100})
    assert edit.status_code == 200 and D(edit.json()["amount"]) == D(6100)

    # only the owner can void, and it needs a reason; the record stays
    assert (
        client.post(
            f"/expenses/{e['id']}/void", headers=manager, json={"reason": "typo"}
        ).status_code
        == 403
    )
    assert (
        client.post(
            f"/expenses/{e['id']}/void", headers=owner, json={"reason": "x"}
        ).status_code
        == 422
    )
    voided = client.post(
        f"/expenses/{e['id']}/void", headers=owner, json={"reason": "entered twice"}
    )
    assert voided.status_code == 200 and voided.json()["voided_at"]
    total = client.get(
        "/expenses/summary?start_date=2001-03-01&end_date=2001-03-31", headers=owner
    ).json()["total"]
    assert D(total) == D(900)
    listed = client.get(
        "/expenses/?start_date=2001-03-01&end_date=2001-03-31&include_voided=true",
        headers=owner,
    ).json()
    assert len(listed) == 2
    assert (
        client.patch(
            f"/expenses/{e['id']}", headers=owner, json={"amount": 1}
        ).status_code
        == 409
    )


def test_expense_validation_and_roles(client, people):
    owner = people["owner"]["h"]
    bad = {"spent_on": "2001-03-05", "category": "Tools"}
    assert (
        client.post("/expenses/", headers=owner, json={**bad, "amount": 0}).status_code
        == 422
    )
    assert (
        client.post("/expenses/", headers=owner, json={**bad, "amount": -5}).status_code
        == 422
    )
    for role in ("sales", "achari", "worker"):
        h = people[role]["h"]
        assert client.get("/expenses/", headers=h).status_code == 403
        assert (
            client.post("/expenses/", headers=h, json={**bad, "amount": 5}).status_code
            == 403
        )
    assert client.get("/expenses/").status_code == 401


# ----------------------------------------------------------------- payroll


def test_pay_out_with_piece_work_overtime_and_advance_recovery(
    client, people, db, staff
):
    owner = people["owner"]["h"]
    uid = str(staff.id)
    work_day(db, staff, date(2002, 5, 1))  # 800
    work_day(db, staff, date(2002, 5, 2), "half-day")  # 400
    work_day(db, staff, date(2002, 5, 20))  # after the cut-off: stays pending

    piece = client.post(
        "/payroll/work-entries",
        headers=owner,
        json={
            "user_id": uid,
            "work_date": "2002-05-02",
            "kind": "piece",
            "description": "Carved Ganesha panel",
            "quantity": 2,
            "rate": 1500,
        },
    )
    assert piece.status_code == 201, piece.text
    assert D(piece.json()["amount"]) == D(3000)
    overtime = client.post(
        "/payroll/work-entries",
        headers=owner,
        json={
            "user_id": uid,
            "work_date": "2002-05-02",
            "kind": "overtime",
            "description": "Rush order",
            "quantity": 2.5,
        },
    )  # rate defaults to the person's Rs 100/hour
    assert overtime.status_code == 201 and D(overtime.json()["amount"]) == D(250)
    client.post(
        "/payroll/work-entries",
        headers=owner,
        json={
            "user_id": uid,
            "work_date": "2002-05-10",
            "kind": "bonus",
            "description": "Festival bonus",
            "quantity": 9,  # ignored: a bonus is one lump sum
            "rate": 500,
        },
    )

    advance = client.post(
        "/payroll/advances",
        headers=owner,
        json={"user_id": uid, "given_on": "2002-04-25", "amount": 1000},
    )
    assert advance.status_code == 201

    owed = next(
        r
        for r in client.get("/payroll/summary", headers=owner).json()
        if r["user_id"] == uid
    )
    assert D(owed["pending_daily"]) == D(2000)  # 800 + 400 + 800
    assert D(owed["pending_extras"]) == D(3750)
    assert D(owed["advance_balance"]) == D(1000)

    paid = client.post(
        "/payroll/pay",
        headers=owner,
        json={
            "user_id": uid,
            "through": "2002-05-15",
            "paid_on": "2002-05-16",
            "deduction": 600,
            "method": "upi",
        },
    )
    assert paid.status_code == 201, paid.text
    p = paid.json()
    assert D(p["gross_daily"]) == D(1200)
    assert D(p["gross_extras"]) == D(3750)
    assert D(p["deductions"]) == D(600)
    assert D(p["net_paid"]) == D(4350)

    me_as_worker = {
        "Authorization": "Bearer "
        + __import__("app.core.security", fromlist=["x"]).create_access_token(
            {"sub": uid}
        )
    }
    mine = client.get("/payroll/me", headers=me_as_worker).json()
    assert D(mine["pending_daily"]) == D(800)  # the 20 May day only
    assert D(mine["pending_extras"]) == D(0)
    assert D(mine["advance_balance"]) == D(400)  # 1000 lent - 600 taken back
    assert len(mine["recent_payments"]) == 1

    ledger = client.get(f"/payroll/advances/{uid}", headers=owner).json()
    assert D(ledger["balance"]) == D(400)
    assert sorted(D(x["amount"]) for x in ledger["entries"]) == [D(-600), D(1000)]


def test_pay_cannot_happen_twice_or_over_deduct(client, people, db, staff):
    owner = people["owner"]["h"]
    uid = str(staff.id)
    work_day(db, staff, date(2003, 1, 1))
    pay = {"user_id": uid, "through": "2003-01-31"}

    # no advance on record: nothing to take back
    over = client.post("/payroll/pay", headers=owner, json={**pay, "deduction": 50})
    assert over.status_code == 400 and "advance" in over.json()["detail"].lower()

    client.post(
        "/payroll/advances",
        headers=owner,
        json={"user_id": uid, "given_on": "2003-01-01", "amount": 5000},
    )
    # cannot take back more than the pay itself (Rs 800)
    big = client.post("/payroll/pay", headers=owner, json={**pay, "deduction": 900})
    assert big.status_code == 400 and "pay due" in big.json()["detail"]

    ok = client.post("/payroll/pay", headers=owner, json={**pay, "deduction": 800})
    assert ok.status_code == 201 and D(ok.json()["net_paid"]) == D(0)
    again = client.post("/payroll/pay", headers=owner, json=pay)
    assert again.status_code == 400  # nothing left, so no double payment


def test_advance_repayment_in_cash_and_limits(client, people, staff):
    owner = people["owner"]["h"]
    uid = str(staff.id)
    give = {"user_id": uid, "given_on": "2003-02-01", "amount": 1000}
    client.post("/payroll/advances", headers=owner, json=give)
    too_much = client.post(
        "/payroll/advances",
        headers=owner,
        json={**give, "kind": "recovery", "amount": 1500},
    )
    assert too_much.status_code == 400
    back = client.post(
        "/payroll/advances",
        headers=owner,
        json={**give, "kind": "recovery", "amount": 400},
    )
    assert back.status_code == 201 and D(back.json()["amount"]) == D(-400)
    assert D(
        client.get(f"/payroll/advances/{uid}", headers=owner).json()["balance"]
    ) == D(600)
    assert (
        client.post(
            "/payroll/advances", headers=owner, json={**give, "amount": 0}
        ).status_code
        == 422
    )


def test_work_entry_rules(client, people, staff):
    owner = people["owner"]["h"]
    base = {
        "user_id": str(staff.id),
        "work_date": "2003-03-01",
        "description": "job",
    }
    # a piece job needs a rate
    assert (
        client.post(
            "/payroll/work-entries", headers=owner, json={**base, "kind": "piece"}
        ).status_code
        == 400
    )
    # overtime with no rate set on the person and none given is refused
    nobody = client.post(  # a fresh person with no overtime rate
        "/auth/register",
        headers=owner,
        json={
            "username": f"n_{uuid.uuid4().hex[:6]}",
            "email": f"n_{uuid.uuid4().hex[:6]}@example.com",
            "password": "StartPass123",
            "role": "worker",
        },
    ).json()
    r = client.post(
        "/payroll/work-entries",
        headers=owner,
        json={**base, "user_id": nobody["id"], "kind": "overtime", "quantity": 2},
    )
    assert r.status_code == 400 and "overtime rate" in r.json()["detail"]
    # a pending entry can be removed, a paid one cannot
    e = client.post(
        "/payroll/work-entries",
        headers=owner,
        json={**base, "kind": "bonus", "rate": 100},
    ).json()
    assert (
        client.delete(f"/payroll/work-entries/{e['id']}", headers=owner).status_code
        == 204
    )
    e2 = client.post(
        "/payroll/work-entries",
        headers=owner,
        json={**base, "kind": "bonus", "rate": 100},
    ).json()
    client.post(
        "/payroll/pay",
        headers=owner,
        json={"user_id": str(staff.id), "through": "2003-03-31"},
    )
    gone = client.delete(f"/payroll/work-entries/{e2['id']}", headers=owner)
    assert gone.status_code == 409
    # absurd sizes are refused rather than crashing the database
    huge = client.post(
        "/payroll/work-entries",
        headers=owner,
        json={**base, "kind": "piece", "quantity": 99999999, "rate": 99999999},
    )
    assert huge.status_code == 400


def test_payroll_roles_and_privacy(client, people, staff):
    owner = people["owner"]["h"]
    other = people["worker"]
    uid = str(staff.id)
    entry = {
        "user_id": uid,
        "work_date": "2003-04-01",
        "kind": "bonus",
        "description": "x",
        "rate": 10,
    }
    for role in ("sales", "achari", "worker"):
        h = people[role]["h"]
        assert client.get("/payroll/summary", headers=h).status_code == 403
        assert (
            client.post("/payroll/work-entries", headers=h, json=entry).status_code
            == 403
        )
        assert (
            client.post(
                "/payroll/pay",
                headers=h,
                json={"user_id": uid, "through": "2003-04-30"},
            ).status_code
            == 403
        )
        assert (
            client.post(
                "/payroll/advances",
                headers=h,
                json={"user_id": uid, "given_on": "2003-04-01", "amount": 1},
            ).status_code
            == 403
        )
    client.post("/payroll/work-entries", headers=owner, json=entry)
    # a worker asking for someone else's entries/ledger only ever gets their own
    seen = client.get(f"/payroll/work-entries?user_id={uid}", headers=other["h"]).json()
    assert all(x["user_id"] == other["id"] for x in seen)
    ledger = client.get(f"/payroll/advances/{uid}", headers=other["h"]).json()
    assert ledger["entries"] == []
    pays = client.get(f"/payroll/payments?user_id={uid}", headers=other["h"]).json()
    assert all(x["user_id"] == other["id"] for x in pays)


def test_legacy_wage_payments_still_count_in_profit(client, people, db, staff):
    """/wages/pay (old path) must not make wages vanish from the books."""
    owner = people["owner"]["h"]
    work_day(db, staff, date(2004, 6, 1))
    wage = (
        db.query(DailyWage)
        .join(Attendance)
        .filter(Attendance.user_id == staff.id, Attendance.date == date(2004, 6, 1))
        .one()
    )
    r = client.post(
        "/wages/pay", headers=owner, json={"attendance_ids": [str(wage.attendance_id)]}
    )
    assert r.status_code == 200, r.text
    db.expire_all()  # the API wrote through another session
    wage = db.query(DailyWage).filter(DailyWage.id == wage.id).one()
    assert wage.payment_id is None and wage.payment_status == "paid"
    db.query(DailyWage).filter(DailyWage.id == wage.id).update(
        {"paid_at": datetime(2004, 6, 2, 10, 0)}
    )
    db.commit()
    rep = client.get(
        "/reports/profit?start_date=2004-06-01&end_date=2004-06-30", headers=owner
    ).json()
    assert D(rep["wages"]) == D(800)


# ------------------------------------------------------------------ profit


def test_profit_report_adds_it_all_up(client, people, db, staff):
    owner = people["owner"]["h"]
    window = "start_date=2005-01-01&end_date=2005-02-28"
    db.add_all(
        [
            Sale(invoice_number=f"P-{uuid.uuid4().hex[:8]}", total_amount=D(10000),
                 tax_total=D(0), created_at=datetime(2005, 1, 10, 12)),
            Sale(invoice_number=f"P-{uuid.uuid4().hex[:8]}", total_amount=D(5000),
                 tax_total=D(0), created_at=datetime(2005, 2, 5, 12)),
            Sale(invoice_number=f"P-{uuid.uuid4().hex[:8]}", total_amount=D(7777),
                 tax_total=D(0), order_status="cancelled",
                 created_at=datetime(2005, 1, 11, 12)),
            Purchase(total_amount=D(3000), status="confirmed",
                     purchase_date=datetime(2005, 1, 12)),
            Purchase(total_amount=D(999), status="pending",
                     purchase_date=datetime(2005, 1, 13)),
        ]
    )  # fmt: skip
    db.commit()
    client.post(
        "/expenses/",
        headers=owner,
        json={"spent_on": "2005-01-15", "category": "Rent", "amount": 2000},
    )
    wrong = client.post(
        "/expenses/",
        headers=owner,
        json={"spent_on": "2005-01-16", "category": "Rent", "amount": 4242},
    ).json()
    client.post(
        f"/expenses/{wrong['id']}/void", headers=owner, json={"reason": "duplicate"}
    )

    work_day(db, staff, date(2005, 1, 3))
    client.post(
        "/payroll/work-entries",
        headers=owner,
        json={
            "user_id": str(staff.id),
            "work_date": "2005-01-03",
            "kind": "piece",
            "description": "panel",
            "quantity": 1,
            "rate": 700,
        },
    )
    client.post(
        "/payroll/advances",
        headers=owner,
        json={"user_id": str(staff.id), "given_on": "2005-01-01", "amount": 300},
    )
    client.post(
        "/payroll/pay",
        headers=owner,
        json={
            "user_id": str(staff.id),
            "through": "2005-01-31",
            "paid_on": "2005-02-01",
            "deduction": 300,
        },
    )

    rep = client.get(f"/reports/profit?{window}", headers=owner).json()
    assert D(rep["sales"]) == D(15000)  # the cancelled sale is left out
    assert D(rep["purchases"]) == D(3000)  # the unconfirmed one is left out
    # wages count GROSS (800 day + 700 piece); the advance recovery is not a saving
    assert D(rep["wages"]) == D(1500)
    assert D(rep["expenses"]) == D(2000)  # the voided one is left out
    assert D(rep["profit"]) == D(15000 - 3000 - 1500 - 2000)
    assert D(rep["margin_pct"]) == D("56.67")
    jan, feb = rep["months"]
    assert (jan["month"], D(jan["profit"])) == ("2005-01", D(10000 - 3000 - 2000))
    assert (feb["month"], D(feb["wages"]), D(feb["profit"])) == (
        "2005-02",
        D(1500),
        D(5000 - 1500),
    )
    assert rep["expenses_by_category"] == [{"category": "Rent", "total": "2000.00"}]


def test_profit_report_is_owner_and_manager_only(client, people):
    assert (
        client.get("/reports/profit", headers=people["manager"]["h"]).status_code == 200
    )
    for role in ("sales", "achari", "worker"):
        r = client.get("/reports/profit", headers=people[role]["h"])
        assert r.status_code == 403
