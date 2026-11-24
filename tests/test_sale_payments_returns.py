"""Billing B3 (payments on a bill, split payments) and B4 (returns, credit notes)."""

import re
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from app.models.item import Item
from app.services.invoice_service import generate_credit_note_number


def D(value) -> Decimal:
    return Decimal(str(value))


def make_item(client, price=1000, gst=0, stock=10) -> str:
    resp = client.post(
        "/items/",
        json={
            "sku": f"PR-{uuid.uuid4().hex[:8]}",
            "name": f"Test piece {uuid.uuid4().hex[:4]}",
            "selling_price": price,
            "current_stock": stock,
            "gst_percent": gst,
        },
    )
    assert resp.status_code in (200, 201), resp.text
    return resp.json()["id"]


def make_sale(client, lines, **extra) -> dict:
    """lines: [(item_id, quantity)]"""
    body = {"items": [{"item_id": i, "quantity": q} for i, q in lines], **extra}
    resp = client.post("/sales/", json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()


def stock(db, item_id) -> int:
    db.expire_all()
    return db.get(Item, uuid.UUID(item_id)).current_stock


# ------------------------------------------------------------------ B3


def test_overpay_refused(auth_client):
    sale = make_sale(auth_client, [(make_item(auth_client, 1000), 1)])
    r = auth_client.post(
        f"/sales/{sale['id']}/payments", json={"amount": 1200, "method": "cash"}
    )
    assert r.status_code == 422
    assert "left to pay" in r.json()["detail"]

    # and at the till: more than the bill in the first payments
    r = auth_client.post(
        "/sales/",
        json={
            "items": [{"item_id": make_item(auth_client, 500), "quantity": 1}],
            "payments": [
                {"amount": 300, "method": "cash"},
                {"amount": 300, "method": "upi"},
            ],
        },
    )
    assert r.status_code == 422
    assert r.json()["detail"] == "More than the bill"


def test_status_and_method_are_derived(auth_client):
    sale = make_sale(auth_client, [(make_item(auth_client, 1000), 1)])
    assert sale["payment_status"] == "pending"
    assert D(sale["balance_due"]) == D(1000)

    r = auth_client.post(
        f"/sales/{sale['id']}/payments", json={"amount": 400, "method": "cash"}
    ).json()
    assert r["payment_status"] == "partial"
    assert r["payment_method"] == "cash"
    assert D(r["amount_paid"]) == D(400) and D(r["balance_due"]) == D(600)

    r = auth_client.post(
        f"/sales/{sale['id']}/payments",
        json={"amount": 600, "method": "upi", "reference": "ab@upi"},
    ).json()
    assert r["payment_status"] == "paid"
    assert r["payment_method"] == "split"
    assert D(r["balance_due"]) == 0
    assert [p["method"] for p in r["payments"]] == ["cash", "upi"]

    # two payments by the same method are not a split
    sale = make_sale(auth_client, [(make_item(auth_client, 1000), 1)])
    for amount in (300, 700):
        r = auth_client.post(
            f"/sales/{sale['id']}/payments", json={"amount": amount, "method": "card"}
        ).json()
    assert r["payment_method"] == "card" and r["payment_status"] == "paid"


def test_void_payment(auth_client):
    sale = make_sale(auth_client, [(make_item(auth_client, 1000), 1)])
    paid = auth_client.post(
        f"/sales/{sale['id']}/payments", json={"amount": 1000, "method": "cash"}
    ).json()
    pid = paid["payments"][0]["id"]
    assert paid["payment_status"] == "paid"

    url = f"/sales/{sale['id']}/payments/{pid}/void"
    assert auth_client.post(url, json={"reason": ""}).status_code == 422
    assert auth_client.post(url, json={"reason": "   "}).status_code == 422
    r = auth_client.post(url, json={"reason": "typed wrong amount"})
    assert r.status_code == 200
    body = r.json()
    assert body["payment_status"] == "pending" and body["payment_method"] is None
    assert D(body["amount_paid"]) == 0 and D(body["balance_due"]) == D(1000)
    # the row stays, flagged
    assert len(body["payments"]) == 1
    assert body["payments"][0]["voided_at"] is not None
    assert body["payments"][0]["void_reason"] == "typed wrong amount"

    assert auth_client.post(url, json={"reason": "again"}).status_code == 409
    missing = f"/sales/{sale['id']}/payments/{uuid.uuid4()}/void"
    assert auth_client.post(missing, json={"reason": "x"}).status_code == 404
    # the freed balance can be paid again
    r = auth_client.post(
        f"/sales/{sale['id']}/payments", json={"amount": 1000, "method": "upi"}
    )
    assert r.json()["payment_status"] == "paid"


def test_create_sale_with_split_payments(auth_client):
    item = make_item(auth_client, 1000, gst=18)  # 1180 per piece
    sale = make_sale(
        auth_client,
        [(item, 1)],
        payments=[
            {"amount": 500, "method": "cash"},
            {"amount": 680, "method": "upi", "reference": "9876@upi"},
        ],
    )
    assert sale["payment_status"] == "paid" and sale["payment_method"] == "split"
    assert D(sale["amount_paid"]) == D(1180) and D(sale["balance_due"]) == 0
    assert {p["method"] for p in sale["payments"]} == {"cash", "upi"}

    part = make_sale(
        auth_client,
        [(make_item(auth_client, 1000), 1)],
        payments=[{"amount": 250, "method": "cash"}],
    )
    assert part["payment_status"] == "partial" and part["payment_method"] == "cash"
    assert D(part["balance_due"]) == D(750)

    # the same data comes back from the list and the single read
    assert D(auth_client.get(f"/sales/{part['id']}").json()["amount_paid"]) == D(250)

    # a bill with a discount: payments are checked against the discounted total
    disc = make_sale(
        auth_client,
        [(make_item(auth_client, 1000), 1)],
        discount=100,
        payments=[{"amount": 900, "method": "cash"}],
    )
    assert disc["payment_status"] == "paid"


def test_replay_with_client_id_creates_nothing_more(auth_client):
    item = make_item(auth_client, 1000)
    body = {
        "client_id": str(uuid.uuid4()),
        "items": [{"item_id": item, "quantity": 1}],
        "payments": [{"amount": 1000, "method": "cash"}],
    }
    first = auth_client.post("/sales/", json=body).json()
    second = auth_client.post("/sales/", json=body).json()
    assert first["id"] == second["id"]
    assert len(second["payments"]) == 1


def test_old_payment_status_path_is_unchanged(auth_client):
    item = make_item(auth_client, 1000, stock=20)

    paid = make_sale(
        auth_client, [(item, 1)], payment_status="paid", payment_method="cash"
    )
    assert paid["payment_status"] == "paid" and paid["payment_method"] == "cash"
    assert len(paid["payments"]) == 1
    assert D(paid["payments"][0]["amount"]) == D(paid["total_amount"])

    partial = make_sale(
        auth_client, [(item, 1)], payment_status="partial", payment_method="cash"
    )
    assert partial["payment_status"] == "partial"
    assert partial["payments"] == []  # the amount is unknown, so no row

    pending = make_sale(auth_client, [(item, 1)], payment_method="upi")
    assert pending["payment_status"] == "pending"
    assert pending["payment_method"] is None  # a method with nothing paid is ignored
    assert pending["payments"] == []

    # PATCH still corrects an old bill that has no payment rows ...
    r = auth_client.patch(f"/sales/{partial['id']}", json={"payment_status": "paid"})
    assert r.status_code == 200 and r.json()["payment_status"] == "paid"
    # ... but not one that has rows
    r = auth_client.patch(f"/sales/{paid['id']}", json={"payment_status": "pending"})
    assert r.status_code == 409 and r.json()["detail"] == "Use the payments"
    # the customer can still be changed on it
    assert auth_client.patch(f"/sales/{paid['id']}", json={}).status_code == 200


def test_cancelled_bill_refuses_payments_and_returns(auth_client):
    item = make_item(auth_client, 1000)
    sale = make_sale(auth_client, [(item, 2)])
    line = sale["items"][0]["id"]
    assert (
        auth_client.delete(f"/sales/{sale['id']}", params={"reason": "mistake"})
    ).status_code == 200

    r = auth_client.post(
        f"/sales/{sale['id']}/payments", json={"amount": 10, "method": "cash"}
    )
    assert r.status_code == 409
    r = auth_client.post(
        f"/sales/{sale['id']}/returns",
        json={
            "items": [{"sale_item_id": line, "quantity": 1}],
            "reason": "x",
            "refund_method": "cash",
        },
    )
    assert r.status_code == 409


# ------------------------------------------------------------------ B4


def do_return(client, sale, qty, line=0, **extra):
    body = {
        "items": [{"sale_item_id": sale["items"][line]["id"], "quantity": qty}],
        "reason": "customer changed mind",
        "refund_method": "cash",
        **extra,
    }
    return client.post(f"/sales/{sale['id']}/returns", json=body)


def test_return_quantity_limits(auth_client):
    sale = make_sale(auth_client, [(make_item(auth_client, 1000), 3)])
    r = do_return(auth_client, sale, 2)
    assert r.status_code == 200, r.text
    r = do_return(auth_client, sale, 2)
    assert r.status_code == 422
    assert "Only 1 left to return of" in r.json()["detail"]
    assert do_return(auth_client, sale, 1).status_code == 200
    assert do_return(auth_client, sale, 1).status_code == 422  # nothing left
    # zero, a line from another bill, and no lines at all
    assert do_return(auth_client, sale, 0).status_code == 422
    other = make_sale(auth_client, [(make_item(auth_client, 100), 1)])
    wrong = {
        "items": [{"sale_item_id": other["items"][0]["id"], "quantity": 1}],
        "reason": "x",
        "refund_method": "cash",
    }
    assert auth_client.post(f"/sales/{sale['id']}/returns", json=wrong).status_code == 422
    empty = {"items": [], "reason": "x", "refund_method": "cash"}
    assert auth_client.post(f"/sales/{sale['id']}/returns", json=empty).status_code == 422
    # the same line twice in one request counts together
    again = make_sale(auth_client, [(make_item(auth_client, 100), 2)])
    line = again["items"][0]["id"]
    body = {
        "items": [
            {"sale_item_id": line, "quantity": 2},
            {"sale_item_id": line, "quantity": 1},
        ],
        "reason": "x",
        "refund_method": "cash",
    }
    assert auth_client.post(f"/sales/{again['id']}/returns", json=body).status_code == 422


def test_refund_is_pro_rata_after_the_bill_discount(auth_client):
    # 1000 + 18% GST, two pieces: line 2360, bill discount 236 -> total 2124
    item = make_item(auth_client, 1000, gst=18)
    sale = make_sale(auth_client, [(item, 2)], discount=236)
    assert D(sale["total_amount"]) == D(2124)
    ret = do_return(auth_client, sale, 1).json()
    assert D(ret["refund_amount"]) == D("1062.00")
    line = ret["items"][0]
    assert D(line["amount"]) == D("1062.00")
    assert D(line["cgst_amount"]) == D("81.00") and D(line["sgst_amount"]) == D("81.00")
    assert D(line["taxable_value"]) == D("900.00")
    assert ret["invoice_number"] == sale["invoice_number"]
    assert ret["restocked"] is True


def test_discount_is_shared_over_lines_by_their_total(auth_client):
    a, b = make_item(auth_client, 1000), make_item(auth_client, 3000)
    sale = make_sale(auth_client, [(a, 1), (b, 1)], discount=400)
    by_price = {ln["price_at_sale"]: ln for ln in sale["items"]}
    line_b = [i for i, ln in enumerate(sale["items"]) if ln is by_price["3000.00"]][0]
    ret = do_return(auth_client, sale, 1, line=line_b).json()
    assert D(ret["refund_amount"]) == D("2700.00")  # 3000 less 3/4 of 400
    line_a = 1 - line_b
    ret = do_return(auth_client, sale, 1, line=line_a).json()
    assert D(ret["refund_amount"]) == D("900.00")


def test_rounding_is_per_line(auth_client):
    # 3 pieces at 100.00: a third of 300 each time is exact; with a discount it is not
    item = make_item(auth_client, 100)
    sale = make_sale(auth_client, [(item, 3)], discount=10)  # paid 290
    refunds = [D(do_return(auth_client, sale, 1).json()["refund_amount"]) for _ in range(3)]
    assert refunds == [D("96.67")] * 3  # each rounded to the paisa on its own


def test_restock_on_and_off(auth_client, db):
    item = make_item(auth_client, 1000, stock=10)
    sale = make_sale(auth_client, [(item, 3)])
    assert stock(db, item) == 7

    r = do_return(auth_client, sale, 1)  # restock defaults to true
    assert r.json()["restocked"] is True and stock(db, item) == 8

    r = do_return(auth_client, sale, 1, restock=False)  # damaged goods
    assert r.json()["restocked"] is False and stock(db, item) == 8

    from app.models.inventory_movement import InventoryMovement

    note = r.json()["credit_note_number"]
    first = [
        m
        for m in db.query(InventoryMovement).filter(
            InventoryMovement.reference_id == uuid.UUID(sale["id"]),
            InventoryMovement.movement_type == "finished_in",
        )
    ]
    assert len(first) == 1 and first[0].notes.startswith("Sales return CN/")
    assert note not in first[0].notes  # the damaged return moved nothing


def test_cancelling_after_a_restocked_return_does_not_restock_twice(auth_client, db):
    item = make_item(auth_client, 1000, stock=10)
    sale = make_sale(auth_client, [(item, 3)])
    do_return(auth_client, sale, 1)  # back on the shelf: 8
    assert stock(db, item) == 8
    auth_client.delete(f"/sales/{sale['id']}", params={"reason": "whole bill void"})
    assert stock(db, item) == 10  # not 11


def test_credit_note_numbering(auth_client, db):
    sale = make_sale(auth_client, [(make_item(auth_client, 100), 3)])
    first = do_return(auth_client, sale, 1).json()["credit_note_number"]
    second = do_return(auth_client, sale, 1).json()["credit_note_number"]
    pattern = r"CN/(\d\d)-(\d\d)/(\d{4})"
    m1, m2 = re.fullmatch(pattern, first), re.fullmatch(pattern, second)
    assert m1 and m2
    assert int(m2.group(3)) == int(m1.group(3)) + 1
    assert int(m1.group(2)) == (int(m1.group(1)) + 1) % 100

    # a failed return gives its number back (no gaps)
    assert do_return(auth_client, sale, 5).status_code == 422
    third = do_return(auth_client, sale, 1).json()["credit_note_number"]
    assert int(re.fullmatch(pattern, third).group(3)) == int(m2.group(3)) + 1

    # each 1 April starts again at 0001 (years far away so no other test shares them)
    assert generate_credit_note_number(db, date(2031, 3, 31)) == "CN/30-31/0001"
    assert generate_credit_note_number(db, date(2031, 4, 1)) == "CN/31-32/0001"
    assert generate_credit_note_number(db, date(2031, 12, 31)) == "CN/31-32/0002"
    assert generate_credit_note_number(db, date(2032, 3, 31)) == "CN/31-32/0003"
    db.commit()


def test_sale_shows_what_was_returned(auth_client):
    sale = make_sale(auth_client, [(make_item(auth_client, 500), 4)])
    line = sale["items"][0]["id"]
    assert sale["returned_total"] == "0.00" and sale["returned_quantities"] == {}
    do_return(auth_client, sale, 1)
    do_return(auth_client, sale, 2)
    got = auth_client.get(f"/sales/{sale['id']}").json()
    assert D(got["returned_total"]) == D(1500)
    assert got["returned_quantities"] == {line: 3}
    # the list reads the same
    listed = [
        s for s in auth_client.get("/sales/?limit=100").json() if s["id"] == sale["id"]
    ]
    assert not listed or listed[0]["returned_quantities"] == {line: 3}

    notes = auth_client.get(f"/sales/{sale['id']}/returns").json()
    assert len(notes) == 2
    assert [D(n["refund_amount"]) for n in notes] == [D(1000), D(500)]  # newest first
    assert notes[0]["items"][0]["item_name"]
    # the bill itself is not edited
    assert got["total_amount"] == sale["total_amount"]
    assert got["invoice_number"] == sale["invoice_number"]
    assert (
        auth_client.get(f"/sales/{uuid.uuid4()}/returns").status_code == 404
    )


def test_credit_note_pdf(auth_client):
    sale = make_sale(auth_client, [(make_item(auth_client, 500, gst=18), 2)])
    number = do_return(auth_client, sale, 1).json()["credit_note_number"]
    r = auth_client.get(f"/returns/{number}/pdf")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/pdf"
    assert r.content.startswith(b"%PDF")
    # a client that encodes the slashes gets the same document
    encoded = auth_client.get(f"/returns/{number.replace('/', '%2F')}/pdf")
    assert encoded.status_code == 200 and encoded.content.startswith(b"%PDF")
    assert auth_client.get("/returns/CN/99-00/9999/pdf").status_code == 404


def test_reports_subtract_credit_notes(auth_client):
    today = date.today().isoformat()
    params = {"start_date": today, "end_date": today}
    gst0 = auth_client.get("/reports/gst-summary", params=params).json()
    sales0 = auth_client.get("/reports/sales", params=params).json()

    sale = make_sale(auth_client, [(make_item(auth_client, 1000, gst=18), 2)])
    gst1 = auth_client.get("/reports/gst-summary", params=params).json()
    sales1 = auth_client.get("/reports/sales", params=params).json()
    assert D(gst1["cgst_total"]) - D(gst0["cgst_total"]) == D(180)

    do_return(auth_client, sale, 1)  # refund 1180: 1000 + 90 + 90
    gst2 = auth_client.get("/reports/gst-summary", params=params).json()
    sales2 = auth_client.get("/reports/sales", params=params).json()
    assert D(gst1["cgst_total"]) - D(gst2["cgst_total"]) == D(90)
    assert D(gst1["sgst_total"]) - D(gst2["sgst_total"]) == D(90)
    assert D(gst1["taxable_value"]) - D(gst2["taxable_value"]) == D(1000)
    assert D(sales1["total_revenue"]) - D(sales2["total_revenue"]) == D(1180)
    assert D(sales1["total_tax"]) - D(sales2["total_tax"]) == D(180)
    assert sales2["total_orders"] == sales1["total_orders"]
    assert D(sales2["total_revenue"]) - D(sales0["total_revenue"]) == D(1180)


# ------------------------------------------------------------------ sync


def test_offline_sale_with_discount_and_split_payments(auth_client):
    item = make_item(auth_client, 1000)
    sale_id = str(uuid.uuid4())
    op = {
        "id": str(uuid.uuid4()),
        "entity": "sale",
        "action": "create",
        "payload": {
            "id": sale_id,
            "items": [{"product_id": item, "quantity": 1, "price": 1000}],
            "total_amount": 900,
            "payment_method": "split",
            "discount": 100,
            "payments": [
                {"amount": 400, "method": "cash"},
                {"amount": 500, "method": "upi"},
            ],
        },
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    resp = auth_client.post("/sync/push", json={"operations": [op]})
    assert resp.status_code == 200, resp.text
    assert resp.json()["failed"] == []
    sale = auth_client.get(f"/sales/{sale_id}").json()
    assert D(sale["total_amount"]) == D(900) and D(sale["discount"]) == D(100)
    assert sale["payment_status"] == "paid" and sale["payment_method"] == "split"
    assert len(sale["payments"]) == 2


# ------------------------------------------------------------------ permissions


def test_payment_and_return_permissions(client_as):
    owner, _ = client_as("owner")
    sale = make_sale(owner, [(make_item(owner, 1000), 2)])
    line = sale["items"][0]["id"]
    ret_body = {
        "items": [{"sale_item_id": line, "quantity": 1}],
        "reason": "x",
        "refund_method": "cash",
    }
    pay = {"amount": 500, "method": "cash"}

    # sales desk: may take a payment and read credit notes, not void or return
    desk, _ = client_as("sales")
    added = desk.post(f"/sales/{sale['id']}/payments", json=pay)
    assert added.status_code == 200
    pid = added.json()["payments"][0]["id"]
    assert (
        desk.post(f"/sales/{sale['id']}/payments/{pid}/void", json={"reason": "x"})
    ).status_code == 403
    assert desk.post(f"/sales/{sale['id']}/returns", json=ret_body).status_code == 403

    # manager: both
    boss, _ = client_as("manager")
    assert (
        boss.post(f"/sales/{sale['id']}/payments/{pid}/void", json={"reason": "x"})
    ).status_code == 200
    made = boss.post(f"/sales/{sale['id']}/returns", json=ret_body)
    assert made.status_code == 200
    number = made.json()["credit_note_number"]
    assert boss.get(f"/returns/{number}/pdf").status_code == 200

    desk, _ = client_as("sales")
    assert desk.get(f"/returns/{number}/pdf").status_code == 200

    # a worker has none of it
    worker, _ = client_as("worker")
    assert worker.post(f"/sales/{sale['id']}/payments", json=pay).status_code == 403
    assert worker.post(f"/sales/{sale['id']}/returns", json=ret_body).status_code == 403
    assert worker.get(f"/returns/{number}/pdf").status_code == 403
