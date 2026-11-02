"""
Writing the ledger from sales and order payments: the posting rules, doing each record
once, redoing a changed record, and the check against the profit report.

Ledger rows can never be deleted, so every test works in its own year in the 1980s and
runs the projector only over that year.
"""

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.finance import projector, reconcile, service
from app.finance.models import FinanceEntry
from app.models.customer import Customer
from app.models.order import Order
from app.models.order_payment import OrderPayment
from app.models.plant_unit import PlantUnit
from app.models.sale import Sale

D = Decimal


def year(n):
    return 1980 + n


def window(n):
    return date(year(n), 1, 1), date(year(n), 12, 31)


def at(n, month=3, day=10, hour=11):
    return datetime(year(n), month, day, hour, 0, 0)


@pytest.fixture(autouse=True)
def chart_and_open_books(db):
    service.ensure_chart(db)
    db.execute(text("UPDATE finance_settings SET closed_through = NULL"))
    db.commit()
    yield
    db.rollback()
    db.execute(text("UPDATE finance_settings SET closed_through = NULL"))
    db.commit()


def make_sale(db, n, total, tax="0", method="cash", status="paid", when=None, **kw):
    sale = Sale(
        invoice_number=f"T-{uuid.uuid4().hex[:10]}",
        created_at=when or at(n),
        total_amount=D(total),
        tax_total=D(tax),
        payment_method=method,
        payment_status=status,
        order_type=kw.pop("order_type", "in-store"),
        order_status=kw.pop("order_status", "completed"),
        **kw,
    )
    db.add(sale)
    db.commit()
    db.refresh(sale)
    return sale


def make_order(db, sale=None):
    customer = Customer(
        name="Meena", phone="9" + "".join(str(uuid.uuid4().int % 10) for _ in range(9))
    )
    db.add(customer)
    db.flush()
    order = Order(
        order_number=f"ORD-{uuid.uuid4().hex[:8]}",
        customer_id=customer.id,
        status="confirmed",
        sale_id=sale.id if sale else None,
    )
    db.add(order)
    db.commit()
    db.refresh(order)
    return order


def pay(db, order, amount, method, paid_on, created):
    p = OrderPayment(
        order_id=order.id,
        amount=D(amount),
        method=method,
        paid_on=paid_on,
        created_at=created.replace(tzinfo=timezone.utc),
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


def run(db, n, **kw):
    start, end = window(n)
    return projector.project(db, date_from=start, date_to=end, **kw)


def net(db, code, n):
    """What the year's entries did to one account (in its normal direction)."""
    start, end = window(n)
    led = service.ledger(db, code, start, end)
    return led.closing_balance - led.opening_balance


def entries_of(db, source_type, source_id):
    return (
        db.query(FinanceEntry)
        .filter(
            FinanceEntry.source_type == source_type,
            FinanceEntry.source_id == str(source_id),
        )
        .order_by(FinanceEntry.created_at)
        .all()
    )


def lines(db, entry):
    out = service.to_out(db, entry)
    return {(ln.account, ln.debit, ln.credit) for ln in out.lines}


# ===================================================================== counter sales
def test_a_paid_cash_sale_is_cash_in_and_sales_with_the_tax_split_off(db):
    unit = PlantUnit(
        name=f"Shop {uuid.uuid4().hex[:6]}",
        code=f"S{uuid.uuid4().hex[:6]}".upper(),
        latitude=1,
        longitude=1,
        radius_m=10,
        kind="shop",
    )
    db.add(unit)
    db.commit()
    sale = make_sale(db, 1, "1180", tax="180", location_id=unit.id)
    result = run(db, 1)
    assert (result.posted, result.reversed, result.problems) == (1, 0, [])
    [entry] = entries_of(db, "sale", sale.id)
    assert entry.kind == "posting" and entry.entry_date == date(year(1), 3, 10)
    assert lines(db, entry) == {
        ("1000", D("1180"), D("0")),
        ("4000", D("0"), D("1000")),
        ("2300", D("0"), D("180")),
    }
    assert {ln.location_id for ln in service.to_out(db, entry).lines} == {unit.id}
    assert entry.memo == f"Sale {sale.invoice_number}" and entry.source_fingerprint


@pytest.mark.parametrize(
    "method,account,problem",
    [
        ("cash", "1000", None),
        ("CASH", "1000", None),
        ("upi", "1010", None),
        ("card", "1010", None),
        ("bank", "1010", None),
        ("cheque", "1010", None),
        ("barter", "1090", "payment_method_unclear"),
        (None, "1090", "payment_method_unclear"),
        ("", "1090", "payment_method_unclear"),
    ],
)
def test_where_the_money_lands_depends_on_how_it_was_paid(db, method, account, problem):
    sale = make_sale(db, 2, "500", method=method)
    result = run(db, 2)
    [entry] = entries_of(db, "sale", sale.id)
    assert lines(db, entry) == {(account, D("500"), D("0")), ("4000", D("0"), D("500"))}
    mine = [p.code for p in result.problems if p.source_id == str(sale.id)]
    assert mine == ([problem] if problem else [])


def test_an_unpaid_sale_is_owed_by_the_customer_and_a_part_paid_one_is_flagged(db):
    pending = make_sale(db, 3, "700", status="pending", method=None)
    partial = make_sale(db, 3, "900", status="partial", method="cash", when=at(3, 4, 1))
    result = run(db, 3)
    for sale, total in ((pending, "700"), (partial, "900")):
        [entry] = entries_of(db, "sale", sale.id)
        assert lines(db, entry) == {
            ("1100", D(total), D("0")),
            ("4000", D("0"), D(total)),
        }
    assert [(p.code, p.source_id) for p in result.problems] == [
        ("partial_sale_amount_unknown", str(partial.id))
    ]
    assert net(db, "1100", 3) == D("1600") and net(db, "4000", 3) == D("1600")


def test_cancelled_and_empty_sales_are_not_posted(db):
    cancelled = make_sale(db, 4, "400", order_status="cancelled")
    zero = make_sale(db, 4, "0")
    result = run(db, 4)
    assert result.posted == 0 and result.nothing_to_post == 2
    assert (
        entries_of(db, "sale", cancelled.id) == []
        and entries_of(db, "sale", zero.id) == []
    )


# ============================================================ once, and again if changed
def test_running_twice_posts_each_record_once(db):
    sale = make_sale(db, 5, "250")
    first, second = run(db, 5), run(db, 5)
    assert (first.posted, second.posted, second.unchanged) == (1, 0, 1)
    assert len(entries_of(db, "sale", sale.id)) == 1
    assert net(db, "4000", 5) == D("250")


def test_a_changed_sale_is_reversed_and_posted_again(db):
    sale = make_sale(db, 6, "1000", method="cash")
    run(db, 6)
    sale.total_amount = D("1200")
    sale.payment_method = "upi"
    db.commit()
    result = run(db, 6)
    assert (result.posted, result.reversed) == (1, 1)
    entries = entries_of(db, "sale", sale.id)
    assert [e.kind for e in entries] == ["posting", "reversal", "posting"]
    assert net(db, "4000", 6) == D("1200")  # only the new version counts
    assert net(db, "1000", 6) == D("0") and net(db, "1010", 6) == D("1200")
    assert run(db, 6).unchanged == 1  # and it settles


def test_a_sale_cancelled_later_is_taken_out_of_the_books(db):
    sale = make_sale(db, 7, "600")
    run(db, 7)
    sale.order_status = "cancelled"
    db.commit()
    result = run(db, 7)
    assert (result.posted, result.reversed) == (0, 1)
    assert net(db, "4000", 7) == D("0") and net(db, "1000", 7) == D("0")
    assert run(db, 7).reversed == 0  # nothing left to undo


def test_only_records_in_the_window_are_touched(db):
    early = make_sale(db, 8, "100", when=at(8, 1, 5))
    late = make_sale(db, 8, "200", when=at(8, 11, 5))
    projector.project(db, date_from=date(year(8), 1, 1), date_to=date(year(8), 6, 30))
    assert (
        len(entries_of(db, "sale", early.id)) == 1
        and entries_of(db, "sale", late.id) == []
    )
    run(db, 8)
    assert (
        len(entries_of(db, "sale", late.id)) == 1
        and len(entries_of(db, "sale", early.id)) == 1
    )


def test_a_run_never_leaves_its_lock_held(db):
    start, end = window(40)
    projector.project(db, date_from=start, date_to=end)
    projector.project(db, date_from=start, date_to=end, dry_run=True)
    held = db.execute(
        text(
            "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' "
            "AND objid = :k AND granted"
        ),
        {"k": projector.LOCK_KEY},
    ).scalar()
    assert held == 0


def test_a_dry_run_counts_but_writes_nothing(db):
    sale = make_sale(db, 9, "100")
    result = run(db, 9, dry_run=True)
    assert result.dry_run and result.posted == 1
    assert entries_of(db, "sale", sale.id) == []


# =============================================================================== orders
def test_an_order_advance_a_bill_and_a_later_payment_clear_out(db):
    when_bill = at(10, 5, 10)
    sale = make_sale(
        db,
        10,
        "3000",
        order_type="order",
        status="partial",
        method="cash",
        when=when_bill,
    )
    order = make_order(db, sale)
    advance = pay(db, order, "1000", "cash", date(year(10), 5, 1), at(10, 5, 1))
    later = pay(db, order, "2000", "upi", date(year(10), 5, 20), at(10, 5, 20))

    result = run(db, 10)
    assert result.posted == 3 and result.problems == []
    [e_adv] = entries_of(db, "order_payment", advance.id)
    assert lines(db, e_adv) == {
        ("1000", D("1000"), D("0")),
        ("2100", D("0"), D("1000")),
    }
    [e_sale] = entries_of(db, "sale", sale.id)
    assert lines(db, e_sale) == {
        ("2100", D("1000"), D("0")),
        ("1100", D("2000"), D("0")),
        ("4000", D("0"), D("3000")),
    }
    [e_late] = entries_of(db, "order_payment", later.id)
    assert lines(db, e_late) == {
        ("1010", D("2000"), D("0")),
        ("1100", D("0"), D("2000")),
    }
    # everything cleared: nothing held, nothing owed, 3000 of sales and of money in
    assert net(db, "2100", 10) == 0 and net(db, "1100", 10) == 0
    assert net(db, "4000", 10) == D("3000")
    assert net(db, "1000", 10) + net(db, "1010", 10) == D("3000")


def test_an_advance_on_an_order_not_yet_billed_is_held_for_it(db):
    order = make_order(db)
    p = pay(db, order, "500", "upi", date(year(11), 2, 2), at(11, 2, 2))
    run(db, 11)
    [entry] = entries_of(db, "order_payment", p.id)
    assert lines(db, entry) == {("1010", D("500"), D("0")), ("2100", D("0"), D("500"))}
    assert net(db, "2100", 11) == D("500")


def test_more_advance_than_the_bill_leaves_the_rest_held(db):
    sale = make_sale(
        db, 12, "3000", order_type="order", status="paid", when=at(12, 6, 10)
    )
    order = make_order(db, sale)
    pay(db, order, "4000", "cash", date(year(12), 6, 1), at(12, 6, 1))
    run(db, 12)
    [entry] = entries_of(db, "sale", sale.id)
    assert lines(db, entry) == {
        ("2100", D("3000"), D("0")),
        ("4000", D("0"), D("3000")),
    }
    assert net(db, "2100", 12) == D("1000")  # still owed back to the customer


def test_a_billed_order_with_no_order_record_is_flagged_and_treated_as_owed(db):
    sale = make_sale(db, 13, "800", order_type="order", status="pending")
    result = run(db, 13)
    assert [p.code for p in result.problems] == ["order_missing"]
    [entry] = entries_of(db, "sale", sale.id)
    assert lines(db, entry) == {("1100", D("800"), D("0")), ("4000", D("0"), D("800"))}


def test_a_payment_with_an_unclear_method_goes_to_clearing_and_is_flagged(db):
    order = make_order(db)
    p = pay(db, order, "300", "other", date(year(14), 2, 2), at(14, 2, 2))
    result = run(db, 14)
    assert [x.code for x in result.problems] == ["payment_method_unclear"]
    [entry] = entries_of(db, "order_payment", p.id)
    assert ("1090", D("300"), D("0")) in lines(db, entry)


# ================================================================ closed books
def test_a_record_in_a_closed_month_is_reported_not_posted(db):
    sale = make_sale(db, 15, "100", when=at(15, 2, 10))
    db.execute(
        text("UPDATE finance_settings SET closed_through = :d"),
        {"d": date(year(15), 2, 28)},
    )
    db.commit()
    result = run(db, 15)
    assert result.posted == 0 and [p.code for p in result.problems] == ["cannot_post"]
    assert entries_of(db, "sale", sale.id) == []
    db.execute(text("UPDATE finance_settings SET closed_through = NULL"))
    db.commit()
    assert run(db, 15).posted == 1


def test_changing_a_sale_in_a_closed_month_reverses_today_and_does_not_repost(db):
    sale = make_sale(db, 16, "100", when=at(16, 2, 10))
    run(db, 16)
    db.execute(
        text("UPDATE finance_settings SET closed_through = :d"),
        {"d": date(year(16), 2, 28)},
    )
    db.commit()
    sale.total_amount = D("150")
    db.commit()
    result = run(db, 16)
    assert result.reversed == 1 and [p.code for p in result.problems] == ["cannot_post"]
    reversal = [e for e in entries_of(db, "sale", sale.id) if e.kind == "reversal"][0]
    assert reversal.entry_date == date.today()  # the closed month was left alone


# ============================================================== the shadow-month check
def test_the_ledger_agrees_with_the_profit_report_only_once_everything_is_posted(db):
    make_sale(db, 17, "1180", tax="180", when=at(17, 1, 5))
    make_sale(db, 17, "700", status="pending", method=None, when=at(17, 1, 6))
    make_sale(db, 17, "999", order_status="cancelled", when=at(17, 1, 7))
    make_sale(db, 17, "400", method="upi", when=at(17, 2, 7))
    billed = make_sale(
        db, 17, "3000", order_type="order", status="partial", when=at(17, 2, 10)
    )
    order = make_order(db, billed)
    pay(db, order, "1000", "cash", date(year(17), 2, 1), at(17, 2, 1))
    pay(db, order, "2000", "upi", date(year(17), 3, 1), at(17, 3, 1))
    start, end = window(17)

    before = reconcile.reconcile(db, start, end)
    assert before.agrees is False and before.to_post == 6
    jan = next(m for m in before.months if m.month == f"{year(17)}-01")
    assert jan.report_sales == D("1700.00") and jan.ledger_sales == D("0.00")
    assert jan.sales_difference == D("-1700.00")

    run(db, 17)
    after = reconcile.reconcile(db, start, end)
    assert after.agrees is True and after.to_post == 0 and after.to_reverse == 0
    by_month = {m.month: m for m in after.months}
    assert by_month[f"{year(17)}-01"].report_sales == D(
        "1700.00"
    )  # 1000 + 700, tax left out
    assert by_month[f"{year(17)}-02"].report_sales == D("3400.00")
    assert all(
        m.sales_difference == 0 and m.payments_difference == 0 for m in after.months
    )
    assert by_month[f"{year(17)}-02"].order_payments == D("1000.00")
    assert by_month[f"{year(17)}-03"].ledger_order_payments == D("2000.00")

    billed.total_amount = D("3500")  # a bill changes after posting
    db.commit()
    drift = reconcile.reconcile(db, start, end)
    assert drift.agrees is False and drift.to_reverse == 1 and drift.to_post == 1
    assert next(
        m for m in drift.months if m.month == f"{year(17)}-02"
    ).sales_difference == D("-500.00")
    run(db, 17)
    assert reconcile.reconcile(db, start, end).agrees is True


# ==================================================================== routes and roles
def test_only_the_owner_runs_the_sync_the_manager_can_read_the_checks(client_as, db):
    sale = make_sale(db, 18, "321", method="mystery")
    start, end = window(18)
    body = {"date_from": start.isoformat(), "date_to": end.isoformat()}

    manager, _ = client_as("manager")
    assert manager.post("/finance/sync", json=body).status_code == 403
    assert (
        manager.get(f"/finance/reconciliation?from={start}&to={end}").status_code == 200
    )
    seen = manager.get(f"/finance/exceptions?from={start}&to={end}")
    assert seen.status_code == 200
    assert [(p["code"], p["source_id"]) for p in seen.json()] == [
        ("payment_method_unclear", str(sale.id))
    ]
    assert entries_of(db, "sale", sale.id) == []  # reading wrote nothing

    for role in ("sales", "achari", "worker"):
        other, _ = client_as(role)
        assert other.post("/finance/sync", json=body).status_code == 403
        assert other.get("/finance/reconciliation").status_code == 403
        assert other.get("/finance/exceptions").status_code == 403

    owner, boss = client_as("owner")
    out = owner.post("/finance/sync", json=body)
    assert out.status_code == 200, out.text
    assert (
        out.json()["posted"] == 1
        and out.json()["problems"][0]["code"] == "payment_method_unclear"
    )
    [entry] = entries_of(db, "sale", sale.id)
    assert entry.created_by == boss.id
    again = owner.post("/finance/sync", json=body).json()
    assert again["posted"] == 0 and again["unchanged"] == 1

    rec = owner.get(f"/finance/reconciliation?from={start}&to={end}").json()
    assert rec["agrees"] is True and rec["to_post"] == 0
    assert rec["months"][0]["report_sales"] == "321.00"
