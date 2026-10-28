"""Processing orders: customer details, search, payments (advance and balance) and edits."""

import uuid
from decimal import Decimal

import pytest


def D(x):
    return Decimal(str(x))


def new_customer(client, h, name=None, phone=None):
    r = client.post(
        "/customers/",
        headers=h,
        json={
            "name": name or f"Customer {uuid.uuid4().hex[:6]}",
            "phone": phone or f"9{uuid.uuid4().int % 10**9:09d}",
            "customer_type": "retail",
        },
    )
    assert r.status_code in (200, 201), r.text
    return r.json()


def new_order(client, h, customer=None, estimate=10000, advance=0, **extra):
    body = {
        "order_type": "custom",
        "items": [
            {"item_name": "Carved temple door", "quantity": 1, "unit_price": estimate}
        ],
        "estimated_amount": estimate,
        "advance_paid": advance,
        **extra,
    }
    if customer:
        body["customer_id"] = customer["id"]
    r = client.post("/orders/", headers=h, json=body)
    assert r.status_code == 201, r.text
    return r.json()


def pay(client, h, order, amount, **extra):
    return client.post(
        f"/orders/{order['id']}/payments", headers=h, json={"amount": amount, **extra}
    )


def test_every_order_carries_the_customer_name_and_phone(client, people):
    h = people["owner"]["h"]
    phone = f"97{uuid.uuid4().int % 10**8:08d}"
    c = new_customer(client, h, "Lakshmi Devi", phone)
    o = new_order(client, h, c)
    assert o["customer_name"] == "Lakshmi Devi" and o["customer_phone"] == phone
    listed = next(
        x
        for x in client.get("/orders/?limit=100", headers=h).json()
        if x["id"] == o["id"]
    )
    assert listed["customer_name"] == "Lakshmi Devi"
    walk_in = new_order(client, h)  # no customer on file
    assert walk_in["customer_name"] is None and walk_in["customer_phone"] is None


def test_search_by_order_number_customer_name_or_phone(client, people):
    h = people["sales"]["h"]
    tag = uuid.uuid4().hex[:6]
    c = new_customer(
        client,
        people["owner"]["h"],
        f"Venkat {tag}",
        f"98{uuid.uuid4().int % 10**8:08d}",
    )
    mine = new_order(client, h, c)
    other = new_order(client, h, new_customer(client, h, f"Someone Else {tag[::-1]}"))

    def found(text):
        return {
            o["id"]
            for o in client.get(f"/orders/?search={text}&limit=100", headers=h).json()
        }

    assert mine["id"] in found(f"Venkat {tag}") and other["id"] not in found(
        f"Venkat {tag}"
    )
    assert mine["id"] in found(mine["order_number"])
    assert mine["id"] in found(c["phone"][-6:])
    assert mine["id"] in found(f"venkat {tag}".upper())  # any case
    assert found("zzz-nothing-matches-this") == set()


def test_payments_build_up_the_advance_and_keep_a_history(client, people):
    h = people["owner"]["h"]
    o = new_order(client, h, new_customer(client, h), estimate=10000)
    first = pay(client, h, o, 3000, method="upi", reference="UPI-123", note="advance")
    assert first.status_code == 201, first.text
    assert D(first.json()["advance_paid"]) == D(3000) and D(
        first.json()["balance_due"]
    ) == D(7000)
    second = pay(
        client, people["sales"]["h"], o, "2500.50"
    )  # sales can take money at the counter
    assert second.status_code == 201
    body = second.json()
    assert D(body["advance_paid"]) == D("5500.50") and D(body["balance_due"]) == D(
        "4499.50"
    )
    assert [(p["method"], D(p["amount"])) for p in body["payments"]] == [
        ("upi", D(3000)),
        ("cash", D("2500.50")),
    ]
    assert (
        body["payments"][0]["reference"] == "UPI-123"
        and body["payments"][0]["note"] == "advance"
    )
    assert (
        client.get(f"/orders/{o['id']}", headers=h).json()["payments"]
        == body["payments"]
    )  # also on the detail


def test_cannot_pay_more_than_is_due_and_the_balance_clears_exactly(client, people):
    h = people["owner"]["h"]
    o = new_order(
        client, h, estimate=5000, advance=1000
    )  # an advance entered when the order was made
    over = pay(client, h, o, 4000.01)
    assert over.status_code == 400 and "balance due" in over.json()["detail"]
    ok = pay(client, h, o, 4000)
    assert ok.status_code == 201 and D(ok.json()["balance_due"]) == D(0)
    assert pay(client, h, o, 1).status_code == 400  # nothing left to pay
    # the final amount takes over from the estimate once it is set
    o2 = new_order(client, h, estimate=5000)
    assert (
        client.patch(
            f"/orders/{o2['id']}", headers=h, json={"final_amount": 6000}
        ).status_code
        == 200
    )
    assert pay(client, h, o2, 6000).status_code == 201


def test_a_deposit_can_be_taken_before_the_order_has_a_price(client, people):
    h = people["owner"]["h"]
    o = new_order(client, h, estimate=0)
    r = pay(client, h, o, 500)
    assert r.status_code == 201 and D(r.json()["advance_paid"]) == D(500)


def test_payment_validation_and_roles(client, people):
    owner = people["owner"]["h"]
    o = new_order(client, owner, estimate=10000)
    assert pay(client, owner, o, 0).status_code == 422
    assert pay(client, owner, o, -5).status_code == 422
    assert pay(client, owner, o, 100, method="bitcoin").status_code == 422
    for role in ("achari", "worker", "artisan"):
        assert pay(client, people[role]["h"], o, 100).status_code == 403
    assert (
        client.post(
            f"/orders/{uuid.uuid4()}/payments", headers=owner, json={"amount": 5}
        ).status_code
        == 404
    )
    assert (
        client.post(f"/orders/{o['id']}/payments", json={"amount": 5}).status_code
        == 401
    )


def test_a_cancelled_order_takes_no_payment(client, people):
    h = people["owner"]["h"]
    o = new_order(client, h)
    assert (
        client.patch(
            f"/orders/{o['id']}/status",
            headers=h,
            json={"status": "cancelled", "notes": "customer changed mind"},
        ).status_code
        == 200
    )
    r = pay(client, h, o, 100)
    assert r.status_code == 409 and "cancelled" in r.json()["detail"]


def test_staff_edit_amounts_delivery_and_notes_of_an_open_order(client, people):
    h = people["manager"]["h"]
    o = new_order(client, h, estimate=8000, advance=2000)
    r = client.patch(
        f"/orders/{o['id']}",
        headers=h,
        json={
            "estimated_amount": 9000,
            "final_amount": 9500,
            "expected_delivery": "2026-12-01",
            "notes": "Add brass hinges",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert D(body["estimated_amount"]) == D(9000) and D(body["final_amount"]) == D(9500)
    assert (
        body["expected_delivery"] == "2026-12-01"
        and body["notes"] == "Add brass hinges"
    )
    assert D(body["balance_due"]) == D(7500)  # final amount minus what was already paid
    only = client.patch(
        f"/orders/{o['id']}", headers=h, json={"notes": "Brass hinges, antique finish"}
    ).json()
    assert D(only["final_amount"]) == D(9500) and only["notes"].endswith(
        "antique finish"
    )  # unsent fields stay


def test_edit_rules(client, people):
    owner = people["owner"]["h"]
    o = new_order(client, owner, estimate=8000, advance=3000)
    low = client.patch(f"/orders/{o['id']}", headers=owner, json={"final_amount": 2000})
    assert low.status_code == 400 and "already paid" in low.json()["detail"]
    assert (
        client.patch(
            f"/orders/{o['id']}", headers=owner, json={"estimated_amount": -1}
        ).status_code
        == 422
    )
    for role in ("sales", "achari", "worker"):
        assert (
            client.patch(
                f"/orders/{o['id']}", headers=people[role]["h"], json={"notes": "x"}
            ).status_code
            == 403
        )
    assert (
        client.patch(
            f"/orders/{uuid.uuid4()}", headers=owner, json={"notes": "x"}
        ).status_code
        == 404
    )
    for status in ("cancelled",):
        client.patch(
            f"/orders/{o['id']}/status", headers=owner, json={"status": status}
        )
    done = client.patch(
        f"/orders/{o['id']}", headers=owner, json={"notes": "late change"}
    )
    assert done.status_code == 409 and "cancelled" in done.json()["detail"]


@pytest.mark.parametrize("role", ["owner", "manager", "sales"])
def test_the_order_screen_roles_can_list_and_read(client, people, role):
    o = new_order(client, people["owner"]["h"])
    assert client.get("/orders/", headers=people[role]["h"]).status_code == 200
    assert (
        client.get(f"/orders/{o['id']}", headers=people[role]["h"]).status_code == 200
    )
