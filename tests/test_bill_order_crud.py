"""
Bill history and orders: edit and delete, with the stock and the money following.

Deleting a bill cancels it (it stays in the history with the reason); the stock comes
back, the reports stop counting it, and the order it came from is freed. A wrong order
payment is voided, not erased.
"""

import uuid
from datetime import date
from decimal import Decimal

import pytest

from app.models.inventory_movement import InventoryMovement
from app.models.order_payment import OrderPayment
from tests.test_order_invoice import bill, catalogue_order, confirm, new_item, stock
from tests.test_order_processing import new_customer, new_order, pay

D = Decimal


def counter_sale(client, h, item, qty, **extra):
    r = client.post(
        "/sales/",
        headers=h,
        json={"items": [{"item_id": item["id"], "quantity": qty}], **extra},
    )
    assert r.status_code == 200, r.text
    return r.json()


def cancel(client, h, sale, reason="entered by mistake"):
    return client.delete(f"/sales/{sale['id']}", headers=h, params={"reason": reason})


def movements(db, sale_id):
    db.expire_all()
    return (
        db.query(InventoryMovement)
        .filter(InventoryMovement.reference_id == uuid.UUID(sale_id))
        .order_by(InventoryMovement.created_at)
        .all()
    )


def revenue_today(client, h):
    today = date.today()
    r = client.get(f"/reports/sales?start_date={today}&end_date={today}", headers=h)
    assert r.status_code == 200, r.text
    return D(str(r.json()["total_revenue"]))


# ======================================================================= bills
def test_deleting_a_bill_puts_every_piece_back_and_keeps_the_bill_as_cancelled(
    client, people, db
):
    h = people["owner"]["h"]
    stool, chair = new_item(client, h, current_stock=10), new_item(
        client, h, current_stock=4
    )
    sale = client.post(
        "/sales/",
        headers=h,
        json={
            "items": [
                {"item_id": stool["id"], "quantity": 3},
                {"item_id": chair["id"], "quantity": 2},
            ]
        },
    ).json()
    assert (stock(client, h, stool), stock(client, h, chair)) == (7, 2)

    r = cancel(client, h, sale, "customer returned everything")
    assert r.status_code == 200, r.text
    assert r.json()["order_status"] == "cancelled" and r.json()["id"] == sale["id"]
    assert (stock(client, h, stool), stock(client, h, chair)) == (10, 4)

    kinds = [(m.movement_type, int(m.quantity)) for m in movements(db, sale["id"])]
    assert sorted(kinds) == sorted(
        [
            ("finished_out", -3),
            ("finished_out", -2),
            ("finished_in", 3),
            ("finished_in", 2),
        ]
    )
    again = client.get(f"/sales/{sale['id']}", headers=h)
    assert again.status_code == 200 and again.json()["order_status"] == "cancelled"
    assert any(s["id"] == sale["id"] for s in client.get("/sales/?limit=100", headers=h).json())  # fmt: skip


def test_a_deleted_bill_leaves_the_sales_reports(client, people):
    h = people["owner"]["h"]
    item = new_item(client, h, current_stock=10)
    before = revenue_today(client, h)
    sale = counter_sale(client, h, item, 2)
    assert revenue_today(client, h) == before + D(sale["total_amount"])
    cancel(client, h, sale)
    assert revenue_today(client, h) == before


def test_a_bill_can_only_be_deleted_once_with_a_reason_by_the_owner(client, people):
    owner = people["owner"]["h"]
    item = new_item(client, owner, current_stock=5)
    sale = counter_sale(client, owner, item, 1)
    assert client.delete(f"/sales/{sale['id']}", headers=owner).status_code == 422
    assert cancel(client, owner, sale, "ab").status_code == 422  # too short
    for role in ("manager", "sales", "worker"):
        assert cancel(client, people[role]["h"], sale).status_code == 403
    assert stock(client, owner, item) == 4  # nothing happened yet
    assert cancel(client, owner, sale).status_code == 200
    again = cancel(client, owner, sale)
    assert again.status_code == 409 and stock(client, owner, item) == 5  # not twice
    missing = client.delete(f"/sales/{uuid.uuid4()}", headers=owner, params={"reason": "x" * 5})  # fmt: skip
    assert missing.status_code == 404


def test_a_sale_made_again_after_the_delete_sees_the_stock_back(client, people):
    h = people["owner"]["h"]
    item = new_item(client, h, current_stock=2)
    sale = counter_sale(client, h, item, 2)
    assert client.post("/sales/", headers=h, json={"items": [{"item_id": item["id"], "quantity": 1}]}).status_code == 400  # fmt: skip
    cancel(client, h, sale)
    assert counter_sale(client, h, item, 2)["order_status"] != "cancelled"
    assert stock(client, h, item) == 0


def test_a_bill_cancelled_with_an_offline_id_is_not_made_again_by_a_replay(
    client, people
):
    h = people["owner"]["h"]
    item = new_item(client, h, current_stock=5)
    cid = str(uuid.uuid4())
    sale = counter_sale(client, h, item, 1, client_id=cid)
    cancel(client, h, sale)
    replay = counter_sale(client, h, item, 1, client_id=cid)
    assert replay["id"] == sale["id"] and replay["order_status"] == "cancelled"
    assert stock(client, h, item) == 5


def test_the_customer_and_payment_of_a_bill_can_be_corrected(client, people):
    owner, manager, sales = (people[r]["h"] for r in ("owner", "manager", "sales"))
    item = new_item(client, owner, current_stock=5)
    sale = counter_sale(client, owner, item, 1, payment_status="pending")
    who = new_customer(client, owner)

    r = client.patch(
        f"/sales/{sale['id']}",
        headers=manager,
        json={
            "customer_id": who["id"],
            "payment_status": "paid",
            "payment_method": "upi",
        },
    )
    assert r.status_code == 200, r.text
    got = r.json()
    assert (got["customer_id"], got["payment_status"], got["payment_method"]) == (who["id"], "paid", "upi")  # fmt: skip
    assert D(got["total_amount"]) == D(sale["total_amount"]) and stock(client, owner, item) == 4  # fmt: skip

    assert client.patch(f"/sales/{sale['id']}", headers=sales, json={"payment_status": "pending"}).status_code == 403  # fmt: skip
    bad = client.patch(
        f"/sales/{sale['id']}", headers=owner, json={"payment_status": "free"}
    )
    assert bad.status_code == 422
    nobody = client.patch(f"/sales/{sale['id']}", headers=owner, json={"customer_id": str(uuid.uuid4())})  # fmt: skip
    assert nobody.status_code == 404
    cancel(client, owner, sale)
    frozen = client.patch(f"/sales/{sale['id']}", headers=owner, json={"payment_status": "pending"})  # fmt: skip
    assert frozen.status_code == 409


def test_deleting_the_bill_of_an_order_frees_the_order_and_keeps_its_payments(
    client, people
):
    h = people["owner"]["h"]
    item = new_item(client, h, current_stock=5)
    order = confirm(client, h, catalogue_order(client, h, [(item, 2, 1000)], 2000))
    assert pay(client, h, order, 500, method="upi").status_code == 201
    first = bill(client, h, order).json()
    assert stock(client, h, item) == 3

    # a billed order cannot be cancelled until its bill is
    blocked = client.patch(f"/orders/{order['id']}/status", headers=h, json={"status": "cancelled"})  # fmt: skip
    assert blocked.status_code == 409

    assert cancel(client, h, first).status_code == 200
    assert stock(client, h, item) == 5
    freed = client.get(f"/orders/{order['id']}", headers=h).json()
    assert freed["sale_id"] is None and D(freed["advance_paid"]) == D("500")
    assert len(freed["payments"]) == 1

    second = bill(client, h, order)  # billed again, a new bill
    assert second.status_code == 201, second.text
    assert second.json()["id"] != first["id"]
    assert D(second.json()["total_amount"]) == D("2000")
    assert stock(client, h, item) == 3
    # the old one is still in the history, cancelled
    assert client.get(f"/sales/{first['id']}", headers=h).json()["order_status"] == "cancelled"  # fmt: skip


def test_the_stock_held_for_an_order_is_held_again_when_its_bill_is_deleted(
    client, people
):
    h = people["owner"]["h"]
    item = new_item(client, h, current_stock=3)
    order = confirm(client, h, catalogue_order(client, h, [(item, 2, 1000)], 2000))
    sale = bill(client, h, order).json()
    assert cancel(client, h, sale).status_code == 200
    # 3 on the shelf, 2 held for the order: only one is left to sell
    assert client.post("/sales/", headers=h, json={"items": [{"item_id": item["id"], "quantity": 2}]}).status_code == 400  # fmt: skip
    assert client.post("/sales/", headers=h, json={"items": [{"item_id": item["id"], "quantity": 1}]}).status_code == 200  # fmt: skip


# ===================================================================== orders
def test_an_order_payment_can_be_corrected_and_the_totals_follow(client, people):
    h = people["owner"]["h"]
    order = new_order(client, h, new_customer(client, h), estimate=10000)
    p1 = pay(client, h, order, 3000, method="cash").json()["payments"][0]
    pay(client, h, order, 2000, method="upi")

    r = client.patch(
        f"/orders/{order['id']}/payments/{p1['id']}",
        headers=h,
        json={"amount": 3500, "method": "bank", "reference": "NEFT-1"},
    )
    assert r.status_code == 200, r.text
    assert D(r.json()["advance_paid"]) == D("5500") and D(r.json()["balance_due"]) == D("4500")  # fmt: skip
    fixed = next(p for p in r.json()["payments"] if p["id"] == p1["id"])
    assert (fixed["method"], fixed["reference"]) == ("bank", "NEFT-1")

    too_much = client.patch(f"/orders/{order['id']}/payments/{p1['id']}", headers=h, json={"amount": 9000})  # fmt: skip
    assert too_much.status_code == 400 and "balance due" in too_much.json()["detail"]
    assert client.patch(f"/orders/{order['id']}/payments/{p1['id']}", headers=h, json={"amount": 0}).status_code == 422  # fmt: skip


def test_a_wrong_payment_is_voided_and_stays_on_record(client, people, db):
    h = people["owner"]["h"]
    order = new_order(client, h, new_customer(client, h), estimate=10000)
    pay(client, h, order, 1000)
    wrong = pay(client, h, order, 4000, method="upi").json()["payments"][-1]

    r = client.post(
        f"/orders/{order['id']}/payments/{wrong['id']}/void",
        headers=h,
        json={"reason": "typed on the wrong order"},
    )
    assert r.status_code == 200, r.text
    assert D(r.json()["advance_paid"]) == D("1000")
    gone = next(p for p in r.json()["payments"] if p["id"] == wrong["id"])
    assert gone["voided_at"] and gone["void_reason"] == "typed on the wrong order"
    assert db.query(OrderPayment).filter(OrderPayment.id == uuid.UUID(wrong["id"])).count() == 1  # fmt: skip

    again = client.post(f"/orders/{order['id']}/payments/{wrong['id']}/void", headers=h, json={"reason": "again"})  # fmt: skip
    assert again.status_code == 409
    edit = client.patch(f"/orders/{order['id']}/payments/{wrong['id']}", headers=h, json={"amount": 1})  # fmt: skip
    assert edit.status_code == 409
    # the room it took up can be paid again
    assert D(pay(client, h, order, 9000).json()["balance_due"]) == D("0")


def test_voiding_a_payment_updates_the_bill_of_a_billed_order(client, people):
    h = people["owner"]["h"]
    item = new_item(client, h, current_stock=5)
    order = confirm(client, h, catalogue_order(client, h, [(item, 1, 1000)], 1000))
    paid = pay(client, h, order, 1000, method="upi").json()["payments"][0]
    sale = bill(client, h, order).json()
    assert sale["payment_status"] == "paid"
    client.post(f"/orders/{order['id']}/payments/{paid['id']}/void", headers=h, json={"reason": "bounced"})  # fmt: skip
    assert client.get(f"/sales/{sale['id']}", headers=h).json()["payment_status"] == "pending"  # fmt: skip


@pytest.mark.parametrize("role", ["manager", "sales", "worker"])
def test_only_the_owner_corrects_order_money(client, people, role):
    owner = people["owner"]["h"]
    order = new_order(client, owner, new_customer(client, owner), estimate=1000)
    p = pay(client, owner, order, 100).json()["payments"][0]
    h = people[role]["h"]
    assert client.patch(f"/orders/{order['id']}/payments/{p['id']}", headers=h, json={"amount": 5}).status_code == 403  # fmt: skip
    assert client.post(f"/orders/{order['id']}/payments/{p['id']}/void", headers=h, json={"reason": "abc"}).status_code == 403  # fmt: skip


def test_an_untouched_order_can_be_deleted_and_its_stock_hold_goes(client, people):
    h = people["owner"]["h"]
    item = new_item(client, h, current_stock=3)
    order = confirm(client, h, catalogue_order(client, h, [(item, 2, 1000)], 2000))
    assert client.post("/sales/", headers=h, json={"items": [{"item_id": item["id"], "quantity": 2}]}).status_code == 400  # fmt: skip
    assert client.delete(f"/orders/{order['id']}", headers=h).status_code == 204
    assert client.get(f"/orders/{order['id']}", headers=h).status_code == 404
    assert client.post("/sales/", headers=h, json={"items": [{"item_id": item["id"], "quantity": 2}]}).status_code == 200  # fmt: skip


def test_an_order_with_money_a_bill_or_a_letter_is_kept(client, people):
    h = people["owner"]["h"]
    item = new_item(client, h, current_stock=5)
    paid = confirm(client, h, catalogue_order(client, h, [(item, 1, 1000)], 1000))
    pay(client, h, paid, 100)
    refused = client.delete(f"/orders/{paid['id']}", headers=h)
    assert refused.status_code == 409 and "payments" in refused.json()["detail"]

    billed = confirm(client, h, catalogue_order(client, h, [(item, 1, 1000)], 1000))
    bill(client, h, billed)
    refused = client.delete(f"/orders/{billed['id']}", headers=h)
    assert refused.status_code == 409 and "billed" in refused.json()["detail"]
    assert client.get(f"/orders/{billed['id']}", headers=h).status_code == 200

    assert client.delete(f"/orders/{uuid.uuid4()}", headers=h).status_code == 404
    assert client.delete(f"/orders/{paid['id']}", headers=people["sales"]["h"]).status_code == 403  # fmt: skip


# ============================================================ the money follows
def ledger_net(db, code):
    """What today's entries have done to one account, in its normal direction."""
    from app.finance import service

    service.ensure_chart(db)
    today = date.today()
    led = service.ledger(db, code, today, today)
    return led.closing_balance - led.opening_balance


def sync_today(db):
    from app.finance import projector, service

    service.ensure_chart(db)
    return projector.project(db, date_from=date.today(), date_to=date.today())


def test_the_ledger_takes_a_deleted_bill_out_on_the_next_sync(client, people, db):
    h = people["owner"]["h"]
    item = new_item(client, h, current_stock=5)
    sale = counter_sale(
        client, h, item, 2, payment_status="paid", payment_method="cash"
    )
    net_of_tax = D(sale["total_amount"]) - D(sale["tax_total"])
    sync_today(db)
    sales_after_posting, cash_after_posting = ledger_net(db, "4000"), ledger_net(db, "1000")  # fmt: skip

    assert cancel(client, h, sale).status_code == 200
    result = sync_today(db)
    assert result.reversed >= 1
    assert ledger_net(db, "4000") == sales_after_posting - net_of_tax
    assert ledger_net(db, "1000") == cash_after_posting - D(sale["total_amount"])
    assert sync_today(db).reversed == 0  # settled


def test_the_ledger_takes_a_voided_order_payment_out_on_the_next_sync(
    client, people, db
):
    h = people["owner"]["h"]
    order = new_order(client, h, new_customer(client, h), estimate=10000)
    p = pay(client, h, order, 2500, method="cash").json()["payments"][0]
    sync_today(db)
    advances, cash = ledger_net(db, "2100"), ledger_net(db, "1000")

    client.post(f"/orders/{order['id']}/payments/{p['id']}/void", headers=h, json={"reason": "wrong order"})  # fmt: skip
    sync_today(db)
    assert ledger_net(db, "2100") == advances - D("2500")
    assert ledger_net(db, "1000") == cash - D("2500")
