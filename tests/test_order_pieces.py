"""
Editing the pieces on an open order: change, add, remove. The stock held for a confirmed
order follows, a piece being made cannot be taken off, and a billed order must have its
bill cancelled first.
"""

import uuid
from decimal import Decimal

from sqlalchemy import text

from tests.test_order_invoice import bill, catalogue_order, confirm, new_item, stock
from tests.test_order_processing import new_customer, new_order

D = Decimal


def put_items(client, h, order, lines, **extra):
    return client.put(
        f"/orders/{order['id']}/items", headers=h, json={"items": lines, **extra}
    )


def line(item, qty, price, **extra):
    return {"item_id": item["id"], "quantity": qty, "unit_price": price, **extra}


def sell(client, h, item, qty):
    return client.post(
        "/sales/", headers=h, json={"items": [{"item_id": item["id"], "quantity": qty}]}
    ).status_code


def test_pieces_can_be_changed_added_and_removed_in_one_edit(client, people):
    h = people["owner"]["h"]
    stool, chair, lamp = (new_item(client, h, current_stock=10) for _ in range(3))
    order = catalogue_order(client, h, [(stool, 1, 1000), (chair, 2, 1500)], 4000)
    keep = next(x for x in order["items"] if x["item_id"] == stool["id"])

    r = put_items(
        client,
        h,
        order,
        [
            {**line(stool, 3, 900), "id": keep["id"], "notes": "darker polish"},
            line(lamp, 1, 700),
            {"item_name": "Carved name board", "quantity": 1, "unit_price": 2500},
        ],
        estimated_amount=5900,
    )
    assert r.status_code == 200, r.text
    got = r.json()
    by_name = {(x["item_id"] or x["item_name"]): x for x in got["items"]}
    assert set(by_name) == {
        stool["id"],
        lamp["id"],
        "Carved name board",
    }  # chair is gone
    changed = by_name[stool["id"]]
    assert changed["id"] == keep["id"]  # the same line, changed in place
    assert (changed["quantity"], D(changed["unit_price"]), changed["notes"]) == (3, D("900"), "darker polish")  # fmt: skip
    assert D(got["estimated_amount"]) == D("5900") and D(got["balance_due"]) == D("5900")  # fmt: skip
    assert len(client.get(f"/orders/{order['id']}", headers=h).json()["items"]) == 3


def test_the_price_stays_put_unless_a_new_one_is_sent(client, people):
    h = people["owner"]["h"]
    stool = new_item(client, h, current_stock=5)
    order = catalogue_order(client, h, [(stool, 1, 1000)], 1000)
    r = put_items(client, h, order, [line(stool, 2, 1000)])
    assert r.status_code == 200 and D(r.json()["estimated_amount"]) == D("1000")


def test_a_confirmed_orders_stock_hold_follows_its_pieces(client, people):
    h = people["owner"]["h"]
    a, b = new_item(client, h, current_stock=3), new_item(client, h, current_stock=3)
    order = confirm(client, h, catalogue_order(client, h, [(a, 2, 1000)], 2000))
    assert sell(client, h, a, 2) == 400  # 3 on the shelf, 2 held

    # take the hold down to 1: two are free again
    keep = order["items"][0]["id"]
    assert put_items(client, h, order, [{**line(a, 1, 1000), "id": keep}]).status_code == 200  # fmt: skip
    assert sell(client, h, a, 2) == 200

    # swap to another piece: a is no longer held at all, b is
    assert put_items(client, h, order, [line(b, 2, 1000)]).status_code == 200
    assert sell(client, h, b, 2) == 400
    assert sell(client, h, b, 1) == 200
    # a had 3, 2 sold above, 1 left and nothing held for the order
    assert sell(client, h, a, 1) == 200


def test_a_draft_order_holds_nothing_before_or_after_the_edit(client, people):
    h = people["owner"]["h"]
    a = new_item(client, h, current_stock=2)
    order = catalogue_order(client, h, [(a, 1, 1000)], 1000)  # still a draft
    assert put_items(client, h, order, [line(a, 2, 1000)]).status_code == 200
    assert sell(client, h, a, 2) == 200


def test_the_bill_is_made_from_the_edited_pieces(client, people):
    h = people["owner"]["h"]
    a, b = new_item(client, h, current_stock=5), new_item(client, h, current_stock=5)
    order = confirm(client, h, catalogue_order(client, h, [(a, 1, 1000)], 1000))
    put_items(client, h, order, [line(b, 2, 1500)], estimated_amount=3000)
    sale = bill(client, h, order)
    assert sale.status_code == 201, sale.text
    assert D(sale.json()["total_amount"]) == D("3000")
    assert (stock(client, h, a), stock(client, h, b)) == (5, 3)


def test_a_billed_delivered_or_cancelled_order_is_not_edited(client, people):
    h = people["owner"]["h"]
    a = new_item(client, h, current_stock=9)
    billed = confirm(client, h, catalogue_order(client, h, [(a, 1, 1000)], 1000))
    bill(client, h, billed)
    r = put_items(client, h, billed, [line(a, 2, 1000)])
    assert r.status_code == 409 and "Cancel the bill" in r.json()["detail"]

    cancelled = catalogue_order(client, h, [(a, 1, 1000)], 1000)
    client.patch(f"/orders/{cancelled['id']}/status", headers=h, json={"status": "cancelled"})  # fmt: skip
    r = put_items(client, h, cancelled, [line(a, 2, 1000)])
    assert r.status_code == 409 and "cancelled" in r.json()["detail"]


def test_a_piece_being_made_cannot_be_taken_off_but_can_be_changed(client, people, db):
    h = people["owner"]["h"]
    a, b = new_item(client, h, current_stock=0), new_item(client, h, current_stock=5)
    order = catalogue_order(client, h, [(a, 1, 1000), (b, 1, 500)], 1500)
    job = client.post("/production/jobs", headers=h, json={"order_id": order["id"], "item_id": a["id"], "target_quantity": 1})  # fmt: skip
    assert job.status_code == 201, job.text

    refused = put_items(client, h, order, [line(b, 1, 500)])
    assert refused.status_code == 409 and job.json()["job_number"] in refused.json()["detail"]  # fmt: skip
    assert len(client.get(f"/orders/{order['id']}", headers=h).json()["items"]) == 2

    keep = {x["item_id"]: x["id"] for x in order["items"]}
    ok = put_items(client, h, order, [{**line(a, 2, 1000), "id": keep[a["id"]]}, {**line(b, 1, 500), "id": keep[b["id"]]}])  # fmt: skip
    assert ok.status_code == 200
    # a cancelled job no longer holds the piece on the order
    db.execute(text("UPDATE production_jobs SET status = 'cancelled' WHERE id = :i"), {"i": job.json()["id"]})  # fmt: skip
    db.commit()
    assert put_items(client, h, order, [line(b, 1, 500)]).status_code == 200


def test_bad_edits_are_refused_and_change_nothing(client, people):
    h = people["owner"]["h"]
    a = new_item(client, h, current_stock=5)
    order = catalogue_order(client, h, [(a, 1, 1000)], 1000)
    other = catalogue_order(client, h, [(a, 1, 1000)], 1000)
    mine = order["items"][0]["id"]

    assert put_items(client, h, order, []).status_code == 422
    assert put_items(client, h, order, [{"quantity": 1}]).status_code == 422  # no piece named  # fmt: skip
    assert put_items(client, h, order, [line(a, 0, 1000)]).status_code == 422
    dup = put_items(client, h, order, [{**line(a, 1, 1), "id": mine}, {**line(a, 1, 1), "id": mine}])  # fmt: skip
    assert dup.status_code == 422
    foreign = put_items(client, h, order, [{**line(a, 1, 1), "id": other["items"][0]["id"]}])  # fmt: skip
    assert foreign.status_code == 404
    ghost = put_items(client, h, order, [{**line(a, 1, 1), "id": str(uuid.uuid4())}])
    assert ghost.status_code == 404
    nothing = put_items(
        client, h, order, [{"item_id": str(uuid.uuid4()), "quantity": 1}]
    )
    assert nothing.status_code == 404
    assert put_items(client, h, {"id": str(uuid.uuid4())}, [line(a, 1, 1)]).status_code == 404  # fmt: skip

    now = client.get(f"/orders/{order['id']}", headers=h).json()
    assert [(x["id"], x["quantity"]) for x in now["items"]] == [(mine, 1)]


def test_the_price_cannot_drop_below_what_is_already_paid(client, people):
    h = people["owner"]["h"]
    order = new_order(client, h, new_customer(client, h), estimate=10000)
    client.post(f"/orders/{order['id']}/payments", headers=h, json={"amount": 4000})
    cheap = [{"item_name": "Smaller door", "quantity": 1, "unit_price": 3000}]
    r = put_items(client, h, order, cheap, estimated_amount=3000)
    assert r.status_code == 400 and "already paid" in r.json()["detail"]
    assert D(client.get(f"/orders/{order['id']}", headers=h).json()["estimated_amount"]) == D("10000")  # fmt: skip
    assert put_items(client, h, order, cheap, estimated_amount=4000).status_code == 200


def test_who_may_edit_the_pieces(client, people):
    owner = people["owner"]["h"]
    a = new_item(client, owner, current_stock=5)
    order = catalogue_order(client, owner, [(a, 1, 1000)], 1000)
    for role, code in (("manager", 200), ("sales", 403), ("worker", 403)):
        r = put_items(client, people[role]["h"], order, [line(a, 1, 1000)])
        assert r.status_code == code, (role, r.text)
