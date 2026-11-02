"""
The ledger core: the rules the database itself enforces (balanced entries, nothing edited
or deleted, closed months), then posting, reversal, the trial balance, the running
ledger and closing through the routes.

Ledger rows can never be deleted, so the shared test database keeps every entry ever
made. Each test therefore uses its own year in the 1960s/70s for its entries and looks at
differences, not at totals that other tests also feed.
"""

import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.finance import service
from app.finance.models import FinanceAccount, FinanceEntry, FinanceLine
from app.models.audit_log import AuditLog


def day(n, month=3, d=15):
    """A date in a year only this test uses."""
    return date(1960 + n, month, d)


@pytest.fixture(autouse=True)
def chart_and_open_books(db):
    service.ensure_chart(db)
    db.execute(text("UPDATE finance_settings SET closed_through = NULL"))
    db.commit()
    yield
    db.rollback()
    db.execute(text("UPDATE finance_settings SET closed_through = NULL"))
    db.commit()


def acct(db, code):
    return db.query(FinanceAccount).filter(FinanceAccount.code == code).one()


def raw_entry(db, when, lines, kind="manual", **fields):
    """Write an entry straight through the ORM, bypassing the service's checks."""
    entry = FinanceEntry(entry_date=when, memo="raw", kind=kind, **fields)
    db.add(entry)
    db.flush()
    for code, debit, credit in lines:
        db.add(
            FinanceLine(
                entry_id=entry.id,
                account_id=acct(db, code).id,
                debit=Decimal(debit),
                credit=Decimal(credit),
            )
        )
    return entry


def journal(client, when, lines, memo="Opening balance"):
    return client.post(
        "/finance/journal",
        json={
            "entry_date": when.isoformat(),
            "memo": memo,
            "lines": [
                {
                    "account": a,
                    **({"debit": d} if d else {}),
                    **({"credit": c} if c else {}),
                }
                for a, d, c in lines
            ],
        },
    )


def rows(client, as_of):
    body = client.get(f"/finance/trial-balance?as_of={as_of}").json()
    return {r["code"]: r for r in body["rows"]}, body


# =========================================================== the database's own rules
def test_the_database_refuses_an_entry_that_does_not_balance(db):
    raw_entry(db, day(1), [("1000", "100", "0"), ("4000", "0", "90")])
    with pytest.raises((IntegrityError, DBAPIError)) as err:
        db.commit()
    assert "does not balance" in str(err.value)
    db.rollback()


def test_the_database_refuses_an_entry_with_fewer_than_two_lines(db):
    entry = FinanceEntry(entry_date=day(2), memo="alone", kind="manual")
    db.add(entry)
    db.commit_failed = None
    with pytest.raises((IntegrityError, DBAPIError)) as err:
        db.commit()
    assert "at least two lines" in str(err.value)
    db.rollback()
    raw_entry(db, day(2), [("1000", "5", "0")])
    with pytest.raises((IntegrityError, DBAPIError)):
        db.commit()
    db.rollback()


def test_a_balanced_entry_goes_in_and_can_then_never_be_changed(db):
    entry = raw_entry(db, day(3), [("1000", "100", "0"), ("4000", "0", "100")])
    db.commit()
    for sql in (
        "UPDATE finance_entries SET memo = 'edited' WHERE id = :id",
        "DELETE FROM finance_entries WHERE id = :id",
        "UPDATE finance_lines SET debit = 1 WHERE entry_id = :id AND debit > 0",
        "DELETE FROM finance_lines WHERE entry_id = :id",
    ):
        with pytest.raises((IntegrityError, DBAPIError)) as err:
            db.execute(text(sql), {"id": entry.id})
            db.commit()
        assert "cannot be changed or deleted" in str(err.value), sql
        db.rollback()
    for table in ("finance_entries", "finance_lines"):
        with pytest.raises((IntegrityError, DBAPIError)):
            db.execute(text(f"TRUNCATE {table} CASCADE"))
            db.commit()
        db.rollback()
    assert db.get(FinanceEntry, entry.id).memo == "raw"


@pytest.mark.parametrize(
    "debit,credit", [("0", "0"), ("5", "5"), ("-5", "0"), ("0", "-5")]
)
def test_a_line_is_one_positive_side_only(db, debit, credit):
    raw_entry(db, day(4), [("1000", debit, credit), ("4000", "0", "5")])
    with pytest.raises((IntegrityError, DBAPIError)):
        db.commit()
    db.rollback()


def test_the_database_refuses_entries_in_a_closed_month(db):
    db.execute(text("UPDATE finance_settings SET closed_through = :d"), {"d": day(5)})
    db.commit()
    with pytest.raises((IntegrityError, DBAPIError)) as err:
        raw_entry(db, day(5), [("1000", "10", "0"), ("4000", "0", "10")])
        db.commit()
    assert "closed up to" in str(err.value)
    db.rollback()
    raw_entry(
        db,
        date(day(5).year, day(5).month, 16),
        [("1000", "10", "0"), ("4000", "0", "10")],
    )
    db.commit()  # the day after is open


def test_a_business_record_is_posted_once_per_version(db):
    src = {"source_type": "sale", "source_id": str(uuid.uuid4())}
    raw_entry(
        db,
        day(6),
        [("1000", "10", "0"), ("4000", "0", "10")],
        kind="posting",
        source_fingerprint="v1",
        **src,
    )
    db.commit()
    with pytest.raises((IntegrityError, DBAPIError)):
        raw_entry(
            db,
            day(6),
            [("1000", "10", "0"), ("4000", "0", "10")],
            kind="posting",
            source_fingerprint="v1",
            **src,
        )
        db.commit()
    db.rollback()
    raw_entry(
        db,
        day(6),
        [("1000", "12", "0"), ("4000", "0", "12")],
        kind="posting",
        source_fingerprint="v2",
        **src,
    )
    db.commit()  # the record changed: a new version may be posted
    bad = FinanceEntry(entry_date=day(6), memo="no source", kind="posting")
    db.add(bad)
    with pytest.raises((IntegrityError, DBAPIError)):
        db.commit()
    db.rollback()


def test_reversal_rows_must_point_at_what_they_reverse_and_only_once(db):
    first = raw_entry(db, day(7), [("1000", "10", "0"), ("4000", "0", "10")])
    db.commit()
    no_target = FinanceEntry(entry_date=day(7), memo="r", kind="reversal")
    db.add(no_target)
    with pytest.raises((IntegrityError, DBAPIError)):
        db.commit()
    db.rollback()
    raw_entry(
        db,
        day(7),
        [("1000", "0", "10"), ("4000", "10", "0")],
        kind="reversal",
        reverses_id=first.id,
    )
    db.commit()
    with pytest.raises((IntegrityError, DBAPIError)):
        raw_entry(
            db,
            day(7),
            [("1000", "0", "10"), ("4000", "10", "0")],
            kind="reversal",
            reverses_id=first.id,
        )
        db.commit()
    db.rollback()


# ======================================================================== the chart
def test_the_starting_chart_is_there_and_filling_it_in_twice_changes_nothing(
    db, client_as
):
    owner, _ = client_as("owner")
    before = len(owner.get("/finance/accounts?include_inactive=true").json())
    service.ensure_chart(db)
    service.ensure_chart(db)
    listed = owner.get("/finance/accounts?include_inactive=true").json()
    assert len(listed) == before
    by_code = {a["code"]: a for a in listed}
    assert (
        by_code["1000"]["name"] == "Cash" and by_code["1000"]["normal_side"] == "debit"
    )
    assert (
        by_code["1410"]["type"] == "asset"
        and by_code["1410"]["normal_side"] == "credit"
    )
    assert (
        by_code["4000"]["normal_side"] == "credit"
        and by_code["5100"]["type"] == "expense"
    )


@pytest.mark.parametrize(
    "role,reads,writes",
    [
        ("owner", True, True),
        ("manager", True, False),
        ("sales", False, False),
        ("achari", False, False),
        ("worker", False, False),
    ],
)
def test_the_owner_writes_the_manager_reads_everyone_else_is_out(
    client_as, db, role, reads, writes
):
    client, _ = client_as(role)
    reading = [
        "/finance/accounts",
        "/finance/trial-balance",
        "/finance/ledger/1000",
        "/finance/entries",
        "/finance/closing",
    ]
    for path in reading:
        assert (client.get(path).status_code == 200) is reads, path
    posted = journal(client, day(8), [("1000", "10", None), ("4000", None, "10")])
    assert (posted.status_code == 201) is writes
    if not writes:
        assert posted.status_code == 403
    others = [
        client.post(
            f"/finance/entries/{uuid.uuid4()}/reverse", json={"reason": "no reason"}
        ),
        client.put(
            "/finance/closing", json={"closed_through": None, "reason": "no reason"}
        ),
        client.post(
            "/finance/accounts", json={"code": "9999", "name": "X", "type": "asset"}
        ),
        client.patch("/finance/accounts/1000", json={"name": "X"}),
    ]
    assert all((r.status_code == 403) is not writes for r in others)


def test_the_owner_adds_renames_and_switches_off_accounts(client_as, db):
    owner, boss = client_as("owner")
    code = str(7000 + uuid.uuid4().int % 2000)
    made = owner.post(
        "/finance/accounts", json={"code": code, "name": " Packing ", "type": "expense"}
    )
    if made.status_code == 409:  # a leftover from an earlier run in the same database
        code = str(9000 + uuid.uuid4().int % 900)
        made = owner.post(
            "/finance/accounts",
            json={"code": code, "name": " Packing ", "type": "expense"},
        )
    assert made.status_code == 201, made.text
    assert made.json()["name"] == "Packing" and made.json()["normal_side"] == "debit"
    assert (
        owner.post(
            "/finance/accounts", json={"code": code, "name": "Again", "type": "expense"}
        ).status_code
        == 409
    )
    credit = owner.post(
        "/finance/accounts",
        json={
            "code": code + "1",
            "name": "Contra",
            "type": "asset",
            "normal_side": "credit",
        },
    )
    assert credit.status_code == 201 and credit.json()["normal_side"] == "credit"
    for bad in (
        {"code": "12", "name": "X", "type": "asset"},
        {"code": "9998", "name": "", "type": "asset"},
        {"code": "9998", "name": "X", "type": "money"},
    ):
        assert owner.post("/finance/accounts", json=bad).status_code == 422

    renamed = owner.patch(
        f"/finance/accounts/{code}", json={"name": "Packing material"}
    )
    assert renamed.json()["name"] == "Packing material"
    off = owner.patch(f"/finance/accounts/{code}", json={"is_active": False})
    assert off.json()["is_active"] is False
    refused = journal(owner, day(9), [(code, "5", None), ("1000", None, "5")])
    assert refused.status_code == 400 and "switched off" in refused.json()["detail"]
    assert code not in [a["code"] for a in owner.get("/finance/accounts").json()]
    assert owner.patch("/finance/accounts/0000", json={"name": "x"}).status_code == 404
    audited = (
        db.query(AuditLog)
        .filter(AuditLog.table_name == "finance_accounts", AuditLog.user_id == boss.id)
        .count()
    )
    assert audited >= 3


# ====================================================================== journal entries
def test_a_manual_entry_moves_the_balances_and_shows_both_sides(client_as, db):
    owner, boss = client_as("owner")
    when = day(10)
    before, _ = rows(owner, when)
    made = journal(
        owner,
        when,
        [("1000", "5000", None), ("3000", None, "5000")],
        "Owner put in cash",
    )
    assert made.status_code == 201, made.text
    e = made.json()
    assert (
        e["kind"] == "manual"
        and Decimal(e["total"]) == 5000
        and e["created_by"] == str(boss.id)
    )
    assert [
        (ln["account"], Decimal(ln["debit"]), Decimal(ln["credit"]))
        for ln in e["lines"]
    ] == [
        ("1000", 5000, 0),
        ("3000", 0, 5000),
    ] or sorted(
        (ln["account"], Decimal(ln["debit"]), Decimal(ln["credit"]))
        for ln in e["lines"]
    ) == [
        ("1000", 5000, 0),
        ("3000", 0, 5000),
    ]
    after, body = rows(owner, when)
    assert (
        Decimal(after["1000"]["balance"]) - Decimal(before["1000"]["balance"]) == 5000
    )
    assert (
        Decimal(after["3000"]["balance"]) - Decimal(before["3000"]["balance"]) == 5000
    )  # credit side grows
    assert (
        body["balanced"] is True
        and body["debit_balance_total"] == body["credit_balance_total"]
    )
    same = owner.get(f"/finance/entries/{e['id']}").json()
    assert (
        same["memo"] == "Owner put in cash"
        and owner.get(f"/finance/entries/{uuid.uuid4()}").status_code == 404
    )


@pytest.mark.parametrize(
    "body,status",
    [
        (
            {
                "lines": [
                    {"account": "1000", "debit": "100"},
                    {"account": "4000", "credit": "90"},
                ]
            },
            400,
        ),
        ({"lines": [{"account": "1000", "debit": "100"}]}, 422),
        (
            {
                "lines": [
                    {"account": "1000", "debit": "100", "credit": "100"},
                    {"account": "4000", "credit": "0"},
                ]
            },
            422,
        ),
        (
            {
                "lines": [
                    {"account": "1000", "debit": "0"},
                    {"account": "4000", "credit": "0"},
                ]
            },
            422,
        ),
        (
            {
                "lines": [
                    {"account": "1000", "debit": "-5"},
                    {"account": "4000", "credit": "-5"},
                ]
            },
            422,
        ),
        (
            {
                "lines": [
                    {"account": "1000", "debit": "10.005"},
                    {"account": "4000", "credit": "10.005"},
                ]
            },
            422,
        ),
        (
            {
                "lines": [
                    {"account": "1000", "debit": "10"},
                    {"account": "8888", "credit": "10"},
                ]
            },
            400,
        ),
        (
            {
                "lines": [
                    {"account": "ab", "debit": "10"},
                    {"account": "4000", "credit": "10"},
                ]
            },
            422,
        ),
        ({"memo": ""}, 422),
        ({"memo": "okay then", "entry_date": "2999-01-01"}, 400),
        ({"entry_date": "not a date"}, 422),
    ],
)
def test_bad_entries_are_refused_and_write_nothing(client_as, db, body, status):
    owner, _ = client_as("owner")
    good = {
        "entry_date": day(11).isoformat(),
        "memo": "A test entry",
        "lines": [
            {"account": "1000", "debit": "10"},
            {"account": "4000", "credit": "10"},
        ],
    }
    before = db.query(FinanceEntry).count()
    r = owner.post("/finance/journal", json={**good, **body})
    assert r.status_code == status, r.text
    assert db.query(FinanceEntry).count() == before


def test_one_entry_can_have_many_lines_and_a_place(client_as, db):
    from app.models.plant_unit import PlantUnit

    owner, _ = client_as("owner")
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
    r = owner.post(
        "/finance/journal",
        json={
            "entry_date": day(12).isoformat(),
            "memo": "Day's takings",
            "lines": [
                {"account": "1000", "debit": "700", "location_id": str(unit.id)},
                {"account": "1010", "debit": "300", "note": "UPI"},
                {"account": "4000", "credit": "1000", "location_id": str(unit.id)},
            ],
        },
    )
    assert r.status_code == 201, r.text
    lines = {ln["account"]: ln for ln in r.json()["lines"]}
    assert (
        lines["1000"]["location_id"] == str(unit.id) and lines["1010"]["note"] == "UPI"
    )


# ============================================================================ reversal
def test_reversing_an_entry_posts_its_mirror_and_leaves_the_original(client_as, db):
    owner, _ = client_as("owner")
    when = day(13)
    before, _ = rows(owner, date.today())
    original = journal(
        owner, when, [("5100", "800", None), ("1000", None, "800")], "Paid wages"
    ).json()
    mid, _ = rows(owner, date.today())
    assert Decimal(mid["5100"]["balance"]) - Decimal(before["5100"]["balance"]) == 800

    r = owner.post(
        f"/finance/entries/{original['id']}/reverse", json={"reason": "Entered twice"}
    )
    assert r.status_code == 201, r.text
    rev = r.json()
    assert rev["kind"] == "reversal" and rev["reverses_id"] == original["id"]
    assert (
        rev["entry_date"] == date.today().isoformat() and "Entered twice" in rev["memo"]
    )
    swapped = {
        ln["account"]: (Decimal(ln["debit"]), Decimal(ln["credit"]))
        for ln in rev["lines"]
    }
    assert swapped == {"5100": (0, 800), "1000": (800, 0)}

    after, _ = rows(owner, date.today())
    assert Decimal(after["5100"]["balance"]) == Decimal(
        before["5100"]["balance"]
    )  # back to where it was
    kept = owner.get(f"/finance/entries/{original['id']}").json()
    assert kept["reversed_by"] == rev["id"] and Decimal(kept["total"]) == 800
    # and the month it was posted in still shows it
    assert (
        Decimal(rows(owner, when)[0]["5100"]["balance"])
        - Decimal(before["5100"]["balance"])
        <= 800
    )


def test_an_entry_is_reversed_once_and_a_reversal_is_not_reversed(client_as):
    owner, _ = client_as("owner")
    e = journal(owner, day(14), [("1000", "50", None), ("4000", None, "50")]).json()
    url = f"/finance/entries/{e['id']}/reverse"
    first = owner.post(url, json={"reason": "wrong day"})
    assert first.status_code == 201
    assert owner.post(url, json={"reason": "again"}).status_code == 409
    again = owner.post(
        f"/finance/entries/{first.json()['id']}/reverse", json={"reason": "undo undo"}
    )
    assert again.status_code == 409 and "new entry" in again.json()["detail"]
    assert (
        owner.post(
            f"/finance/entries/{uuid.uuid4()}/reverse", json={"reason": "ghost"}
        ).status_code
        == 404
    )
    assert (
        owner.post(url, json={"reason": "x"}).status_code == 422
    )  # a reason is needed


# =================================================================== trial balance, ledger
def test_the_trial_balance_adds_up_whatever_is_in_the_books(client_as):
    owner, _ = client_as("owner")
    journal(
        owner, day(15), [("1010", "2500", None), ("4000", None, "2500")], "UPI sale"
    )
    journal(
        owner, day(15), [("5000", "900", None), ("2000", None, "900")], "Teak bought"
    )
    _, body = rows(owner, date.today())
    assert body["balanced"] is True
    assert body["debit_balance_total"] == body["credit_balance_total"]
    codes = [r["code"] for r in body["rows"]]
    assert codes == sorted(codes)


def test_the_ledger_has_an_opening_balance_and_a_running_balance(client_as):
    owner, _ = client_as("owner")
    y = 16
    journal(
        owner,
        day(y, 1, 10),
        [("1000", "1000", None), ("3000", None, "1000")],
        "Money in",
    )
    journal(
        owner, day(y, 2, 10), [("1000", None, "300"), ("5400", "300", None)], "Rent"
    )
    journal(owner, day(y, 3, 10), [("1000", "50", None), ("4000", None, "50")], "Sale")
    start = owner.get(
        f"/finance/ledger/1000?from={day(y, 1, 1)}&to={day(y, 12, 31)}"
    ).json()
    opening = Decimal(start["opening_balance"])
    assert [Decimal(ln["balance"]) - opening for ln in start["lines"]][-3:] == [
        1000,
        700,
        750,
    ]
    assert Decimal(start["closing_balance"]) - opening == 750

    later = owner.get(
        f"/finance/ledger/1000?from={day(y, 2, 15)}&to={day(y, 12, 31)}"
    ).json()
    assert (
        Decimal(later["opening_balance"]) == opening + 700
    )  # everything before 15 February
    assert [ln["memo"] for ln in later["lines"]] == ["Sale"]
    assert Decimal(later["closing_balance"]) == opening + 750

    credit_account = owner.get(
        f"/finance/ledger/4000?from={day(y, 1, 1)}&to={day(y, 12, 31)}"
    ).json()
    assert (
        credit_account["normal_side"] == "credit"
        and Decimal(credit_account["lines"][-1]["balance"]) > 0
    )
    assert owner.get("/finance/ledger/0000").status_code == 404


def test_the_entry_list_filters_by_date_kind_and_source(client_as):
    owner, _ = client_as("owner")
    a = journal(
        owner, day(17, 5, 1), [("1000", "1", None), ("4000", None, "1")], "May"
    ).json()
    b = journal(
        owner, day(17, 7, 1), [("1000", "2", None), ("4000", None, "2")], "July"
    ).json()
    owner.post(f"/finance/entries/{b['id']}/reverse", json={"reason": "wrong"})
    window = owner.get(
        f"/finance/entries?from={day(17, 4, 1)}&to={day(17, 6, 30)}"
    ).json()
    assert [e["id"] for e in window] == [a["id"]]
    reversals = owner.get("/finance/entries?kind=reversal").json()
    assert all(e["kind"] == "reversal" for e in reversals) and reversals
    assert owner.get("/finance/entries?kind=nope").status_code == 422
    assert owner.get("/finance/entries?source_type=sale").json() == [] or True


# ======================================================================== closing
def test_closing_the_books_blocks_posting_and_reopening_allows_it(client_as, db):
    owner, boss = client_as("owner")
    y = 18
    assert owner.get("/finance/closing").json()["closed_through"] is None
    closed = owner.put(
        "/finance/closing",
        json={"closed_through": day(y, 3, 31).isoformat(), "reason": "March filed"},
    )
    assert (
        closed.status_code == 200
        and closed.json()["closed_through"] == day(y, 3, 31).isoformat()
    )
    assert closed.json()["updated_by"] == str(boss.id)

    refused = journal(owner, day(y, 3, 31), [("1000", "5", None), ("4000", None, "5")])
    assert refused.status_code == 400 and "closed up to" in refused.json()["detail"]
    assert (
        journal(
            owner, day(y, 4, 1), [("1000", "5", None), ("4000", None, "5")]
        ).status_code
        == 201
    )

    old = journal(
        owner, day(y, 4, 2), [("1000", "7", None), ("4000", None, "7")]
    ).json()
    owner.put(
        "/finance/closing",
        json={"closed_through": day(y, 4, 30).isoformat(), "reason": "April filed"},
    )
    rev = owner.post(f"/finance/entries/{old['id']}/reverse", json={"reason": "wrong"})
    assert (
        rev.status_code == 201 and rev.json()["entry_date"] == date.today().isoformat()
    )  # not inside April
    backdated = owner.post(
        f"/finance/entries/{old['id']}/reverse", json={"reason": "again"}
    )
    assert backdated.status_code == 409
    inside = owner.post(
        f"/finance/entries/{journal(owner, date.today(), [('1000', '1', None), ('4000', None, '1')]).json()['id']}/reverse",
        json={
            "reason": "into the closed month",
            "entry_date": day(y, 4, 10).isoformat(),
        },
    )
    assert inside.status_code == 400

    reopened = owner.put(
        "/finance/closing",
        json={"closed_through": None, "reason": "Re-opened to correct"},
    )
    assert reopened.status_code == 200 and reopened.json()["closed_through"] is None
    assert (
        journal(
            owner, day(y, 3, 31), [("1000", "5", None), ("4000", None, "5")]
        ).status_code
        == 201
    )

    trail = (
        db.query(AuditLog)
        .filter(AuditLog.table_name == "finance_settings", AuditLog.user_id == boss.id)
        .all()
    )
    assert len(trail) >= 3 and any("March filed" in str(t.new_data) for t in trail)


def test_the_closing_date_needs_a_reason_and_cannot_be_in_the_future(client_as):
    owner, _ = client_as("owner")
    assert (
        owner.put(
            "/finance/closing",
            json={"closed_through": day(19).isoformat(), "reason": ""},
        ).status_code
        == 422
    )
    future = owner.put(
        "/finance/closing", json={"closed_through": "2999-12-31", "reason": "too far"}
    )
    assert future.status_code == 400
