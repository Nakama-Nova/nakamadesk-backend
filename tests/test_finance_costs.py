"""
Step D of the finance ledger: purchases, expenses, wages, staff advances and machines
(docs/FINANCE_LEDGER_DESIGN.md, section 4), and the full profit-report reconciliation.

The ledger can never be emptied, so every test uses its own year (1980 + n, n from 21).
"""

import uuid
from datetime import date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.finance import projector, reconcile, service
from app.models.attendance import Attendance
from app.models.daily_wage import DailyWage
from app.models.expense import Expense
from app.models.machine import Machine, MachineMaintenance
from app.models.payroll import PayrollPayment, WorkerAdvance
from app.models.purchase import Purchase
from app.models.supplier import Supplier
from app.models.user import User
from tests.test_finance_projector import (
    at,
    entries_of,
    lines,
    net,
    run,
    window,
    year,
)

D = Decimal


@pytest.fixture(autouse=True)
def chart_and_open_books(db):
    service.ensure_chart(db)
    db.execute(text("UPDATE finance_settings SET closed_through = NULL"))
    db.commit()
    yield
    db.rollback()
    db.execute(text("UPDATE finance_settings SET closed_through = NULL"))
    db.commit()


def add(db, row):
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def make_staff(db):
    uid = uuid.uuid4().hex[:8]
    return add(
        db,
        User(
            username=f"w_{uid}",
            email=f"w_{uid}@example.com",
            password_hash="x",
            role="worker",
        ),
    )


def make_purchase(db, n, total, status="confirmed", paid="pending", month=4):
    supplier = add(db, Supplier(name=f"Timber {uuid.uuid4().hex[:6]}"))
    return add(
        db,
        Purchase(
            invoice_number=f"P-{uuid.uuid4().hex[:10]}",
            purchase_date=at(n, month, 5),
            supplier_id=supplier.id,
            total_amount=D(total),
            status=status,
            payment_status=paid,
        ),
    )


def make_expense(db, n, amount, category="Rent", method="cash", **kw):
    return add(
        db,
        Expense(
            spent_on=date(year(n), 5, 2),
            category=category,
            amount=D(amount),
            method=method,
            **kw,
        ),
    )


def make_payout(db, n, daily, extras, net_paid, method="cash", user=None):
    user = user or make_staff(db)
    return add(
        db,
        PayrollPayment(
            user_id=user.id,
            paid_on=date(year(n), 6, 30),
            through=date(year(n), 6, 30),
            gross_daily=D(daily),
            gross_extras=D(extras),
            deductions=D(daily) + D(extras) - D(net_paid),
            net_paid=D(net_paid),
            method=method,
        ),
    )


# ======================================================================== purchases
def test_a_confirmed_purchase_is_raw_material_bought_and_owed_to_the_supplier(db):
    p = make_purchase(db, 21, "5000")
    result = run(db, 21)
    assert (result.posted, result.problems) == (1, [])
    [entry] = entries_of(db, "purchase", p.id)
    assert entry.entry_date == date(year(21), 4, 5)
    assert lines(db, entry) == {
        ("5000", D("5000"), D("0")),
        ("2000", D("0"), D("5000")),
    }
    assert net(db, "5000", 21) == D("5000")
    assert net(db, "2000", 21) == D("5000")


def test_pending_and_cancelled_purchases_are_not_in_the_books(db):
    make_purchase(db, 22, "900", status="pending")
    make_purchase(db, 22, "800", status="cancelled")
    result = run(db, 22)
    assert result.posted == 0 and result.nothing_to_post >= 2


def test_a_paid_purchase_settles_what_we_owe_out_of_unmatched_payments_and_is_flagged(
    db,
):
    p = make_purchase(db, 23, "2000", paid="paid")
    result = run(db, 23)
    assert result.posted == 2
    assert [x.code for x in result.problems] == ["supplier_payment_unrecorded"]
    [entry] = entries_of(db, "purchase_payment", p.id)
    assert lines(db, entry) == {
        ("2000", D("2000"), D("0")),
        ("1090", D("0"), D("2000")),
    }
    assert net(db, "2000", 23) == D("0")  # owed 2000, then settled


def test_a_part_paid_purchase_stays_owed_and_is_flagged(db):
    p = make_purchase(db, 24, "2000", paid="partial")
    result = run(db, 24)
    assert result.posted == 1
    assert [x.code for x in result.problems] == ["partial_purchase_amount_unknown"]
    assert entries_of(db, "purchase_payment", p.id) == []
    assert net(db, "2000", 24) == D("2000")


def test_a_purchase_cancelled_later_is_taken_out_with_its_payment(db):
    p = make_purchase(db, 25, "1500", paid="paid")
    run(db, 25)
    p.status = "cancelled"
    db.commit()
    result = run(db, 25)
    assert result.reversed == 2
    assert net(db, "5000", 25) == D("0") and net(db, "2000", 25) == D("0")
    assert run(db, 25).reversed == 0


# ========================================================================= expenses
@pytest.mark.parametrize(
    "category,account",
    [
        ("Rent", "5400"),
        ("Electricity", "5400"),
        ("Machine Repairs", "5200"),
        ("Maintenance", "5200"),
        ("Other", "5900"),
        ("", "5900"),
    ],
)
def test_an_expense_goes_to_the_account_for_its_category(category, account):
    assert projector.expense_account(category) == account


def test_an_expense_is_money_out_of_cash_or_the_bank(db):
    cash = make_expense(db, 26, "1200", "Rent", "cash")
    upi = make_expense(db, 26, "300", "Transport", "upi")
    result = run(db, 26)
    assert (result.posted, result.problems) == (2, [])
    assert lines(db, entries_of(db, "expense", cash.id)[0]) == {
        ("5400", D("1200"), D("0")),
        ("1000", D("0"), D("1200")),
    }
    assert lines(db, entries_of(db, "expense", upi.id)[0]) == {
        ("5400", D("300"), D("0")),
        ("1010", D("0"), D("300")),
    }


def test_a_voided_expense_is_reversed_and_an_unclear_method_is_flagged(db):
    e = make_expense(db, 27, "700", "Rent", "cash")
    odd = make_expense(db, 27, "50", "Tea", "barter")
    result = run(db, 27)
    assert [(x.source_id, x.code) for x in result.problems] == [
        (str(odd.id), "payment_method_unclear")
    ]
    e.voided_at = datetime(year(27), 5, 3)
    db.commit()
    assert run(db, 27).reversed == 1
    assert net(db, "5400", 27) == D("50")


# ============================================================================ wages
def test_a_payout_is_gross_wages_with_the_advance_taken_back(db):
    pay = make_payout(db, 28, "5000", "1000", "5200")  # 800 taken back
    result = run(db, 28)
    assert (result.posted, result.problems) == (1, [])
    [entry] = entries_of(db, "payroll_payment", pay.id)
    assert entry.entry_date == date(year(28), 6, 30)
    assert lines(db, entry) == {
        ("5100", D("6000"), D("0")),
        ("1000", D("0"), D("5200")),
        ("1300", D("0"), D("800")),
    }


def test_a_payout_by_upi_comes_out_of_the_bank(db):
    pay = make_payout(db, 29, "3000", "0", "3000", method="upi")
    run(db, 29)
    assert lines(db, entries_of(db, "payroll_payment", pay.id)[0]) == {
        ("5100", D("3000"), D("0")),
        ("1010", D("0"), D("3000")),
    }


def test_a_payout_that_pays_more_than_was_earned_is_reported_not_posted(db):
    pay = make_payout(db, 30, "1000", "0", "1500")
    result = run(db, 30)
    assert result.posted == 0
    assert [x.code for x in result.problems] == ["payout_does_not_add_up"]
    assert entries_of(db, "payroll_payment", pay.id) == []


def test_an_old_style_wage_payment_is_cash_but_one_inside_a_payout_is_not_counted_twice(
    db,
):
    staff = make_staff(db)
    payout = make_payout(db, 31, "400", "0", "400", user=staff)

    def wage(day, **kw):
        att = add(
            db,
            Attendance(
                user_id=staff.id,
                date=date(year(31), 6, day),
                status="present",
                daily_wage=D("400"),
                recorded_by=staff.id,
            ),
        )
        return add(
            db,
            DailyWage(
                attendance_id=att.id,
                amount=D("400"),
                paid_at=at(31, 6, day),
                **kw,
            ),
        )

    alone = wage(1, payment_status="paid")
    inside = wage(2, payment_status="paid", payment_id=payout.id)
    waiting = wage(3, payment_status="pending")
    run(db, 31)
    assert lines(db, entries_of(db, "daily_wage", alone.id)[0]) == {
        ("5100", D("400"), D("0")),
        ("1000", D("0"), D("400")),
    }
    assert entries_of(db, "daily_wage", inside.id) == []
    assert entries_of(db, "daily_wage", waiting.id) == []
    assert net(db, "5100", 31) == D("800")  # the pay-out plus the one on its own


def test_advances_given_and_paid_back_move_cash_and_staff_advances(db):
    staff = make_staff(db)
    given = add(
        db,
        WorkerAdvance(
            user_id=staff.id,
            given_on=date(year(32), 2, 1),
            amount=D("2000"),
            kind="advance",
        ),
    )
    back = add(
        db,
        WorkerAdvance(
            user_id=staff.id,
            given_on=date(year(32), 3, 1),
            amount=D("-500"),
            kind="recovery",
        ),
    )
    payout = make_payout(db, 32, "1000", "0", "800", user=staff)
    inside = add(
        db,
        WorkerAdvance(
            user_id=staff.id,
            given_on=payout.paid_on,
            amount=D("-200"),
            kind="recovery",
            payment_id=payout.id,
        ),
    )
    run(db, 32)
    assert lines(db, entries_of(db, "staff_advance", given.id)[0]) == {
        ("1300", D("2000"), D("0")),
        ("1000", D("0"), D("2000")),
    }
    assert lines(db, entries_of(db, "staff_advance", back.id)[0]) == {
        ("1000", D("500"), D("0")),
        ("1300", D("0"), D("500")),
    }
    assert entries_of(db, "staff_advance", inside.id) == []  # inside the pay-out
    # lent 2000, 500 paid back, 200 taken in the pay-out
    assert net(db, "1300", 32) == D("1300")


# ======================================================================== machines
def test_a_machine_and_its_repairs_post_against_unmatched_payments(db):
    machine = add(
        db,
        Machine(
            tag=f"M-{uuid.uuid4().hex[:6]}",
            name="Lathe",
            purchase_date=date(year(33), 1, 15),
            purchase_cost=D("80000"),
        ),
    )
    job = add(
        db,
        MachineMaintenance(
            machine_id=machine.id,
            kind="repair",
            performed_on=date(year(33), 4, 1),
            cost=D("1500"),
        ),
    )
    free = add(
        db,
        MachineMaintenance(
            machine_id=machine.id,
            kind="scheduled",
            performed_on=date(year(33), 5, 1),
            cost=D("0"),
        ),
    )
    result = run(db, 33)
    assert result.posted == 2
    assert {x.code for x in result.problems} == {"machine_payment_unrecorded"}
    assert lines(db, entries_of(db, "machine", machine.id)[0]) == {
        ("1400", D("80000"), D("0")),
        ("1090", D("0"), D("80000")),
    }
    assert lines(db, entries_of(db, "machine_maintenance", job.id)[0]) == {
        ("5200", D("1500"), D("0")),
        ("1090", D("0"), D("1500")),
    }
    assert entries_of(db, "machine_maintenance", free.id) == []  # cost nothing


def test_a_machine_with_a_cost_but_no_date_is_reported_not_posted(db):
    # no purchase date means it cannot be put in a window, so run with no window
    add(
        db,
        Machine(tag=f"M-{uuid.uuid4().hex[:6]}", name="Saw", purchase_cost=D("100")),
    )
    codes = [x.code for x in projector.project(db, dry_run=True).problems]
    assert "machine_date_missing" in codes


def test_machine_money_is_not_part_of_the_profit_comparison(db):
    add(
        db,
        Machine(
            tag=f"M-{uuid.uuid4().hex[:6]}",
            name="Planer",
            purchase_date=date(year(34), 2, 2),
            purchase_cost=D("50000"),
        ),
    )
    run(db, 34)
    start, end = window(34)
    assert reconcile.reconcile(db, start, end).agrees is True


# ================================================================ whole-report check
def test_the_ledger_agrees_with_the_whole_profit_report_once_everything_is_posted(db):
    make_purchase(db, 35, "5000")
    make_purchase(db, 35, "999", status="pending")
    make_expense(db, 35, "1200", "Rent")
    make_expense(db, 35, "300", "Machine Repairs", "upi")
    make_payout(db, 35, "5000", "1000", "5200")
    start, end = window(35)

    before = reconcile.reconcile(db, start, end)
    assert before.agrees is False and before.to_post == 4
    assert {m.month: m.purchases_difference for m in before.months} == {
        f"{year(35)}-04": D("-5000.00"),
        f"{year(35)}-05": D("0.00"),
        f"{year(35)}-06": D("0.00"),
    }

    run(db, 35)
    after = reconcile.reconcile(db, start, end)
    assert after.agrees is True
    by_month = {m.month: m for m in after.months}
    apr, may, jun = (f"{year(35)}-0{i}" for i in (4, 5, 6))
    assert by_month[apr].report_purchases == D("5000.00")
    assert by_month[may].report_expenses == D("1500.00")
    assert by_month[may].ledger_expenses == D("1500.00")
    assert by_month[jun].report_wages == D("6000.00")
    assert by_month[jun].ledger_wages == D("6000.00")

    pay = db.query(PayrollPayment).filter(PayrollPayment.paid_on >= start).first()
    pay.gross_extras = D("2000")  # a pay-out is corrected after posting
    db.commit()
    drift = reconcile.reconcile(db, start, end)
    assert drift.agrees is False and drift.to_reverse == 1
    assert next(m for m in drift.months if m.month == jun).wages_difference == D(
        "-1000.00"
    )
    run(db, 35)
    assert reconcile.reconcile(db, start, end).agrees is True


def test_changed_costs_are_reversed_and_posted_again_once(db):
    p = make_purchase(db, 36, "1000")
    e = make_expense(db, 36, "100", "Rent")
    run(db, 36)
    assert run(db, 36).unchanged == 2
    p.total_amount = D("1100")
    e.amount = D("120")
    db.commit()
    result = run(db, 36)
    assert (result.posted, result.reversed) == (2, 2)
    assert net(db, "5000", 36) == D("1100") and net(db, "5400", 36) == D("120")
    assert run(db, 36).posted == 0
