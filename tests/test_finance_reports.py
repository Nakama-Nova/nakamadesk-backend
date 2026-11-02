"""
Step E of the finance ledger: profit and loss, balance sheet and the read-only
accountant role (docs/FINANCE_LEDGER_DESIGN.md, section 6).

The ledger can never be emptied, so each test posts into its own past year (1980 + n,
n from 37) and the balance sheet is checked by how much a year changed it.
"""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.finance import reports, service
from app.services import profit_service
from tests.test_finance_costs import make_expense, make_payout, make_purchase
from tests.test_finance_projector import make_sale, run, window, year

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


def amounts(rows):
    return {r.code: r.amount for r in rows}


def a_trading_year(db, n):
    """Cash sale 1180 (1000 + 180 tax), purchase 400 unpaid, rent 300 in cash, wages 600."""
    make_sale(db, n, "1180", tax="180")
    make_purchase(db, n, "400")
    make_expense(db, n, "300", "Rent")
    make_payout(db, n, "500", "100", "600")
    run(db, n)


# ============================================================== profit and loss
def test_profit_and_loss_adds_up_income_and_expenses_by_account(db):
    a_trading_year(db, 37)
    start, end = window(37)
    pl = reports.profit_and_loss(db, start, end)
    assert amounts(pl.income) == {"4000": D("1000.00")}  # the tax is not income
    assert amounts(pl.expenses) == {
        "5000": D("400.00"),
        "5100": D("600.00"),
        "5400": D("300.00"),
    }
    assert pl.total_income == D("1000.00") and pl.total_expenses == D("1300.00")
    assert pl.net_profit == D("-300.00")


def test_profit_and_loss_matches_the_existing_profit_report(db):
    a_trading_year(db, 38)
    start, end = window(38)
    pl = reports.profit_and_loss(db, start, end)
    old = profit_service.profit_report(db, start, end)
    assert pl.net_profit == old["profit"]
    assert pl.total_income == old["sales"]
    assert pl.total_expenses == old["purchases"] + old["wages"] + old["expenses"]


def test_profit_and_loss_only_counts_the_period_asked_for(db):
    a_trading_year(db, 39)
    assert reports.profit_and_loss(db, *window(39)).total_income == D("1000.00")
    other = reports.profit_and_loss(
        db, date(year(39) + 1, 1, 1), date(year(39) + 1, 12, 31)
    )
    assert other.total_income == 0 and other.income == [] and other.net_profit == 0


# ================================================================ balance sheet
def test_the_balance_sheet_balances_and_a_year_moves_it_as_expected(db):
    before = reports.balance_sheet(db, date(year(40) - 1, 12, 31))
    a_trading_year(db, 40)
    # the owner puts 5000 into the business: cash in, owner's capital up
    service.post_entry(
        db,
        entry_date=date(year(40), 1, 2),
        memo="Capital put in",
        lines=[
            {"account": "1000", "debit": D("5000")},
            {"account": "3000", "credit": D("5000")},
        ],
        kind="manual",
        created_by=None,
    )
    after = reports.balance_sheet(db, date(year(40), 12, 31))
    assert before.balanced and after.balanced

    # cash: +1180 sale, +5000 capital, -300 rent, -600 wages (paid in cash)
    assets = {
        c: amounts(after.assets).get(c, D(0)) - amounts(before.assets).get(c, D(0))
        for c in amounts(after.assets)
    }
    assert assets["1000"] == D(
        "5280.00"
    ) and after.total_assets - before.total_assets == D("5280.00")
    # owed: 180 tax to the state, 400 to the supplier
    owed = {
        c: amounts(after.liabilities).get(c, D(0))
        - amounts(before.liabilities).get(c, D(0))
        for c in ("2000", "2300")
    }
    assert owed == {"2000": D("400.00"), "2300": D("180.00")}
    assert after.total_liabilities - before.total_liabilities == D("580.00")
    assert amounts(after.equity)["3000"] - amounts(before.equity).get(
        "3000", D(0)
    ) == D("5000.00")
    # 1000 income - 400 - 300 - 600
    assert after.profit_so_far - before.profit_so_far == D("-300.00")
    assert after.total_equity - before.total_equity == D("4700.00")


def test_the_balance_sheet_only_counts_up_to_the_day_asked_for(db):
    a_trading_year(db, 41)
    early = reports.balance_sheet(db, date(year(41), 1, 1))
    late = reports.balance_sheet(db, date(year(41), 12, 31))
    assert late.total_assets != early.total_assets
    assert early.balanced and late.balanced


# ===================================================== the accountant and the routes
def test_the_accountant_reads_the_books_and_reports_but_changes_nothing(client_as, db):
    a_trading_year(db, 42)
    start, end = window(42)
    read = [
        "/finance/accounts",
        "/finance/trial-balance",
        "/finance/ledger/1000",
        f"/finance/profit-and-loss?from={start}&to={end}",
        "/finance/balance-sheet",
        f"/finance/reconciliation?from={start}&to={end}",
        f"/finance/exceptions?from={start}&to={end}",
        "/finance/closing",
    ]
    client, _ = client_as("accountant")
    for url in read:
        assert client.get(url).status_code == 200, url

    body = {"date_from": start.isoformat(), "date_to": end.isoformat()}
    assert client.post("/finance/sync", json=body).status_code == 403
    assert (
        client.post(
            "/finance/accounts", json={"code": "9999", "name": "X", "type": "expense"}
        ).status_code
        == 403
    )
    assert client.post("/finance/journal", json={}).status_code == 403
    assert client.put("/finance/closing", json={}).status_code == 403


def test_the_manager_sees_profit_and_loss_but_not_the_balance_sheet(client_as):
    client, _ = client_as("manager")
    assert client.get("/finance/profit-and-loss").status_code == 200
    assert client.get("/finance/balance-sheet").status_code == 403


@pytest.mark.parametrize("role", ["sales", "achari", "worker", "artisan"])
def test_everyone_else_is_kept_out_of_the_reports(client_as, role):
    client, _ = client_as(role)
    assert client.get("/finance/profit-and-loss").status_code == 403
    assert client.get("/finance/balance-sheet").status_code == 403


def test_the_owner_reads_the_balance_sheet_over_the_api(client_as):
    client, _ = client_as("owner")
    body = client.get("/finance/balance-sheet?as_of=2020-12-31")
    assert body.status_code == 200
    assert body.json()["balanced"] is True and body.json()["as_of"] == "2020-12-31"
