"""
Letters: edit and delete. Finance ledger: correct an entry typed in by hand.

A letter keeps its number, its date and its letterhead when edited; only a void letter
can be deleted. A manual ledger entry is corrected by reversing it and posting the new
one in a single step, so the ledger is never rewritten.
"""

import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.finance import service
from app.models.audit_log import AuditLog
from app.models.letter import Letter
from tests.test_letters import (
    LINES,
    new_supplier,
    quotation,
    restored_project,
)
from tests.test_order_processing import new_customer, new_order, pay

D = Decimal


# ================================================================= letters: edit
def test_an_edited_quotation_keeps_its_number_and_date_and_changes_its_words(
    client, people, db
):
    h = people["owner"]["h"]
    letter = quotation(client, h)
    r = client.put(
        f"/letters/{letter['id']}",
        headers=h,
        json={
            "party_name": "Sri Rama Temple Trust, Mysuru",
            "lines": [
                {
                    "description": "Carved temple door",
                    "quantity": 1,
                    "unit_price": "40000",
                }
            ],
            "notes": "Price held till the festival",
        },
    )
    assert r.status_code == 200, r.text
    got = r.json()
    assert got["number"] == letter["number"] and got["id"] == letter["id"]
    assert got["issued_on"] == letter["issued_on"] and got["status"] == "issued"
    assert got["party_name"] == "Sri Rama Temple Trust, Mysuru"
    assert D(got["amount"]) == D("40000")

    detail = client.get(f"/letters/{letter['id']}", headers=h).json()
    assert detail["payload"]["total"] == "40000.00"
    assert detail["payload"]["notes"] == "Price held till the festival"
    assert len(detail["payload"]["lines"]) == 1

    log = (
        db.query(AuditLog)
        .filter(AuditLog.table_name == "letters", AuditLog.row_id == letter["id"])
        .all()
    )
    assert [x.action for x in log] == ["edit"]
    assert (
        log[0].old_data["amount"] != log[0].new_data["amount"]
    )  # the old wording is kept


def test_editing_a_letter_does_not_use_up_a_new_number(client, people):
    h = people["owner"]["h"]
    first = quotation(client, h)
    client.put(f"/letters/{first['id']}", headers=h, json={"party_name": "X Trust", "lines": LINES})  # fmt: skip
    second = quotation(client, h)
    a, b = first["number"], second["number"]
    assert int(b.rsplit("-", 1)[1]) == int(a.rsplit("-", 1)[1]) + 1


def test_an_edited_letter_keeps_the_letterhead_it_was_printed_on(client, people):
    h = people["owner"]["h"]
    client.put("/letters/profile", headers=h, json={"name": "Old Name Studios"})
    letter = quotation(client, h)
    client.put("/letters/profile", headers=h, json={"name": "New Name Studios"})
    client.put(f"/letters/{letter['id']}", headers=h, json={"party_name": "Z Trust", "lines": LINES})  # fmt: skip
    head = client.get(f"/letters/{letter['id']}", headers=h).json()["payload"]["letterhead"]  # fmt: skip
    assert head["name"] == "Old Name Studios"
    client.put("/letters/profile", headers=h, json={"name": "Vriksha Studios"})


def test_an_order_confirmation_is_redrawn_from_the_order_as_it_is_now(client, people):
    h = people["owner"]["h"]
    order = new_order(client, h, new_customer(client, h), estimate=10000)
    pay(client, h, order, 2000)
    letter = client.post("/letters/order-confirmation", headers=h, json={"order_id": order["id"]}).json()  # fmt: skip
    pay(client, h, order, 3000)
    r = client.put(f"/letters/{letter['id']}", headers=h, json={"order_id": order["id"], "notes": "Second instalment in"})  # fmt: skip
    assert r.status_code == 200, r.text
    payload = client.get(f"/letters/{letter['id']}", headers=h).json()["payload"]
    assert payload["advance"] == "5000.00" and payload["balance"] == "5000.00"
    assert D(r.json()["amount"]) == D("5000")


def test_a_purchase_receipt_can_be_corrected(client, people):
    h = people["owner"]["h"]
    supplier = new_supplier(client, h)
    body = {
        "supplier_id": supplier["id"],
        "description": "Old carved panel",
        "amount": "8000",
    }
    letter = client.post("/letters/purchase-receipt", headers=h, json=body).json()
    r = client.put(f"/letters/{letter['id']}", headers=h, json={**body, "amount": "8500", "method": "upi"})  # fmt: skip
    assert r.status_code == 200, r.text
    assert D(r.json()["amount"]) == D("8500") and r.json()["number"] == letter["number"]
    assert client.get(f"/letters/{letter['id']}", headers=h).json()["payload"]["payment"]["method"] == "upi"  # fmt: skip


def test_a_completion_certificate_can_be_corrected(client, people):
    h = people["owner"]["h"]
    project = restored_project(client, h)
    body = {"restoration_id": project["id"], "work_done": "Cleaned and re-gilded"}
    letter = client.post("/letters/completion-certificate", headers=h, json=body).json()
    r = client.put(f"/letters/{letter['id']}", headers=h, json={**body, "work_done": "Cleaned, repaired and re-gilded"})  # fmt: skip
    assert r.status_code == 200, r.text
    detail = client.get(f"/letters/{letter['id']}", headers=h).json()
    assert detail["payload"]["work_done"] == "Cleaned, repaired and re-gilded"
    assert detail["number"] == letter["number"]


def test_a_void_letter_cannot_be_edited_and_bad_details_are_refused(client, people):
    h = people["owner"]["h"]
    letter = quotation(client, h)
    bad = client.put(f"/letters/{letter['id']}", headers=h, json={"party_name": "A", "lines": []})  # fmt: skip
    assert bad.status_code == 422 and letter["number"]
    junk = client.put(
        f"/letters/{letter['id']}", headers=h, json={"valid_days": "soon"}
    )
    assert junk.status_code == 422 and isinstance(junk.json()["detail"], list)
    assert client.put(f"/letters/{uuid.uuid4()}", headers=h, json={"party_name": "A", "lines": LINES}).status_code == 404  # fmt: skip
    client.post(
        f"/letters/{letter['id']}/void", headers=h, json={"reason": "wrong party"}
    )
    frozen = client.put(f"/letters/{letter['id']}", headers=h, json={"party_name": "A", "lines": LINES})  # fmt: skip
    assert frozen.status_code == 409


def test_who_may_edit_a_letter_follows_who_may_issue_it(client, people):
    owner = people["owner"]["h"]
    letter = quotation(client, owner)
    body = {"party_name": "Edited by sales", "lines": LINES}
    assert client.put(f"/letters/{letter['id']}", headers=people["sales"]["h"], json=body).status_code == 200  # fmt: skip
    assert client.put(f"/letters/{letter['id']}", headers=people["worker"]["h"], json=body).status_code == 403  # fmt: skip
    # a certificate is the owner's alone: to a manager it does not even exist
    project = restored_project(client, owner)
    cert = client.post("/letters/completion-certificate", headers=owner, json={"restoration_id": project["id"], "work_done": "Done"}).json()  # fmt: skip
    seen = client.put(f"/letters/{cert['id']}", headers=people["manager"]["h"], json={"restoration_id": project["id"], "work_done": "Changed"})  # fmt: skip
    assert seen.status_code == 404


# ============================================================== letters: delete
def test_only_a_void_letter_can_be_deleted_and_what_it_said_is_kept(client, people, db):
    h = people["owner"]["h"]
    letter = quotation(client, h)
    assert client.delete(f"/letters/{letter['id']}", headers=h).status_code == 409
    client.post(f"/letters/{letter['id']}/void", headers=h, json={"reason": "issued twice"})  # fmt: skip
    for role in ("manager", "sales", "worker"):
        assert client.delete(f"/letters/{letter['id']}", headers=people[role]["h"]).status_code == 403  # fmt: skip

    assert client.delete(f"/letters/{letter['id']}", headers=h).status_code == 204
    assert client.get(f"/letters/{letter['id']}", headers=h).status_code == 404
    assert db.query(Letter).filter(Letter.id == uuid.UUID(letter["id"])).count() == 0
    log = (
        db.query(AuditLog)
        .filter(
            AuditLog.table_name == "letters",
            AuditLog.row_id == letter["id"],
            AuditLog.action == "delete",
        )  # fmt: skip
        .one()
    )
    assert log.old_data["number"] == letter["number"] and log.old_data["void_reason"] == "issued twice"  # fmt: skip
    assert client.delete(f"/letters/{letter['id']}", headers=h).status_code == 404


# ================================================================ ledger: edit
@pytest.fixture
def books(db):
    service.ensure_chart(db)
    db.execute(text("UPDATE finance_settings SET closed_through = NULL"))
    db.commit()
    yield
    db.rollback()
    db.execute(text("UPDATE finance_settings SET closed_through = NULL"))
    db.commit()


def journal(day, amount, memo="Capital put in", debit="1000", credit="3000"):
    return {
        "entry_date": day.isoformat(),
        "memo": memo,
        "lines": [
            {"account": debit, "debit": str(amount), "credit": "0"},
            {"account": credit, "debit": "0", "credit": str(amount)},
        ],
    }


def balance(db, code, day):
    led = service.ledger(db, code, None, day)
    return led.closing_balance


def test_a_manual_entry_is_corrected_by_reversing_it_and_posting_the_new_one(
    client_as, db, books
):
    owner, _ = client_as("owner")
    day = date(2019, 5, 10)
    cash, capital = balance(db, "1000", day), balance(db, "3000", day)
    made = owner.post("/finance/journal", json=journal(day, 5000)).json()

    r = owner.put(
        f"/finance/entries/{made['id']}",
        json={**journal(day, 4500, "Capital put in"), "reason": "typed 5000, was 4500"},
    )
    assert r.status_code == 201, r.text
    new = r.json()
    assert new["id"] != made["id"] and new["kind"] == "manual" and D(new["total"]) == D("4500")  # fmt: skip
    assert "corrects the entry of 2019-05-10: typed 5000, was 4500" in new["memo"]

    # net effect: 4500 in, the 5000 undone
    assert balance(db, "1000", day) == cash + D("4500")
    assert balance(db, "3000", day) == capital + D("4500")
    old = owner.get(f"/finance/entries/{made['id']}").json()
    assert (
        old["reversed_by"] is not None and old["kind"] == "manual"
    )  # kept, now undone


def test_a_corrected_entry_can_be_corrected_again_but_not_the_old_one(client_as, db, books):  # fmt: skip
    owner, _ = client_as("owner")
    day = date(2019, 5, 11)
    made = owner.post("/finance/journal", json=journal(day, 100)).json()
    again = owner.put(f"/finance/entries/{made['id']}", json={**journal(day, 200), "reason": "first fix"}).json()  # fmt: skip
    third = owner.put(f"/finance/entries/{again['id']}", json={**journal(day, 300), "reason": "second fix"})  # fmt: skip
    assert third.status_code == 201
    stale = owner.put(f"/finance/entries/{made['id']}", json={**journal(day, 999), "reason": "too late"})  # fmt: skip
    assert stale.status_code == 409 and "already reversed" in stale.json()["detail"]


def test_a_failed_correction_leaves_the_old_entry_exactly_as_it_was(client_as, db, books):  # fmt: skip
    owner, _ = client_as("owner")
    day = date(2019, 5, 12)
    made = owner.post("/finance/journal", json=journal(day, 700)).json()
    before = balance(db, "1000", day)

    unbalanced = journal(day, 700)
    unbalanced["lines"][1]["credit"] = "650"
    assert owner.put(f"/finance/entries/{made['id']}", json={**unbalanced, "reason": "oops"}).status_code == 400  # fmt: skip
    nowhere = journal(day, 700, debit="987654")
    assert owner.put(f"/finance/entries/{made['id']}", json={**nowhere, "reason": "oops"}).status_code == 400  # fmt: skip
    future = journal(date.today().replace(year=date.today().year + 1), 700)
    assert owner.put(f"/finance/entries/{made['id']}", json={**future, "reason": "oops"}).status_code == 400  # fmt: skip

    assert balance(db, "1000", day) == before
    assert owner.get(f"/finance/entries/{made['id']}").json()["reversed_by"] is None


def test_an_entry_made_from_a_record_is_corrected_at_its_record(client_as, db, books):
    from tests.test_finance_projector import make_sale, run

    owner, _ = client_as("owner")
    sale = make_sale(db, 20, "250")  # a cash sale in 2000
    run(db, 20)
    from tests.test_finance_projector import entries_of

    [entry] = entries_of(db, "sale", sale.id)
    r = owner.put(f"/finance/entries/{entry.id}", json={**journal(date(2000, 3, 10), 250), "reason": "change it"})  # fmt: skip
    assert r.status_code == 409 and "sale record" in r.json()["detail"]

    rev = owner.post(f"/finance/entries/{entry.id}/reverse", json={"reason": "undo for the test", "entry_date": "2000-03-11"}).json()  # fmt: skip
    again = owner.put(f"/finance/entries/{rev['id']}", json={**journal(date(2000, 3, 11), 250), "reason": "no reason needed"})  # fmt: skip
    assert again.status_code == 409 and "reversal" in again.json()["detail"]


def test_correcting_an_entry_in_a_closed_month_reverses_it_today(client_as, db, books):
    owner, _ = client_as("owner")
    day = date(2019, 6, 3)
    made = owner.post("/finance/journal", json=journal(day, 800)).json()
    db.execute(text("UPDATE finance_settings SET closed_through = :d"), {"d": date(2019, 6, 30)})  # fmt: skip
    db.commit()
    r = owner.put(f"/finance/entries/{made['id']}", json={**journal(date.today(), 900), "reason": "found later"})  # fmt: skip
    assert r.status_code == 201, r.text
    reversal = next(
        e for e in owner.get("/finance/entries?limit=200").json() if e.get("reverses_id") == made["id"]  # fmt: skip
    )
    assert reversal["entry_date"] == date.today().isoformat()
    # while the new entry cannot be put inside the closed month
    inside = owner.post("/finance/journal", json=journal(day, 1)).status_code
    assert inside == 400


@pytest.mark.parametrize("role", ["manager", "accountant", "sales", "worker"])
def test_only_the_owner_corrects_ledger_entries(client_as, db, books, role):
    owner, _ = client_as("owner")
    made = owner.post("/finance/journal", json=journal(date(2019, 5, 13), 10)).json()
    other, _ = client_as(role)
    r = other.put(f"/finance/entries/{made['id']}", json={**journal(date(2019, 5, 13), 20), "reason": "nope"})  # fmt: skip
    assert r.status_code == 403
