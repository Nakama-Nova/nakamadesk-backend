"""
A signed-in shopper: saved delivery addresses, orders tied to the account, and
seeing only their own orders and addresses.
"""

import random
import re
import uuid
from decimal import Decimal

import pytest

from app.core import ratelimit
from app.core.config import settings
from app.models.customer_account import CustomerAddress
from app.models.item import Item
from app.models.order import Order
from app.services import email_service

# Customers are found by phone and shared by the whole test run, so these numbers are
# random: a fixed one would collide with other tests that make a customer with it.
PHONE = "9" + "".join(random.choice("0123456789") for _ in range(9))
ACCOUNT_PHONE = "9" + "".join(random.choice("0123456789") for _ in range(9))


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    ratelimit.reset()
    monkeypatch.setattr(settings, "DELIVERY_PINCODES", [])
    monkeypatch.setattr(settings, "PUBLIC_RATE_LIMIT", 200)
    yield
    ratelimit.reset()


@pytest.fixture
def outbox(monkeypatch):
    sent = []
    monkeypatch.setattr(
        email_service,
        "send",
        lambda to, subject, body: sent.append((to, subject, body)),
    )
    return sent


def make_item(db, name="Teak Chair", stock=4, price="1000.00", **kw):
    uid = uuid.uuid4().hex[:8]
    item = Item(
        sku=f"ACC-{uid}",
        name=f"{name} {uid}",
        slug=f"{name.lower().replace(' ', '-')}-{uid}",
        selling_price=Decimal(price),
        gst_percent=Decimal("0"),
        current_stock=stock,
        is_published=True,
        is_active=True,
        **kw,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


def shopper(client, outbox, name="Asha Rao"):
    """A signed-in shopper: (auth headers, email)."""
    email = f"shopper_{uuid.uuid4().hex[:10]}@example.com"
    client.post("/account/request-code", json={"email": email})
    code = re.search(r"\b(\d{6})\b", outbox[-1][2]).group(1)
    r = client.post(
        "/account/verify",
        json={"email": email, "code": code, "name": name, "accept_terms": True},
    )
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}, email


ADDRESS = {
    "label": "Home",
    "recipient_name": "Asha Rao",
    "phone": PHONE,
    "address": "12 Temple Road",
    "town": "Puttur",
    "pincode": "574201",
}


def save_address(client, h, **over):
    r = client.post("/account/addresses", headers=h, json={**ADDRESS, **over})
    assert r.status_code == 201, r.text
    return r.json()


def order_with(client, h, item, qty=1, **over):
    return client.post(
        "/account/checkout",
        headers=h,
        json={"items": [{"slug": item.slug, "quantity": qty}], **over},
    )


# ------------------------------------------------------------------- addresses
def test_the_first_address_is_the_default_and_the_default_can_move(client, outbox):
    h, _ = shopper(client, outbox)
    first = save_address(client, h, label="Home")
    second = save_address(client, h, label="Shop", address="5 Market Street")
    assert first["is_default"] is True and second["is_default"] is False

    changed = client.put(
        f"/account/addresses/{second['id']}",
        headers=h,
        json={**ADDRESS, "address": "5 Market Street", "is_default": True},
    )
    assert changed.status_code == 200 and changed.json()["is_default"] is True
    listed = client.get("/account/addresses", headers=h).json()
    assert [a["id"] for a in listed][0] == second["id"]  # default first
    assert [a["is_default"] for a in listed] == [True, False]


def test_deleting_the_default_makes_another_the_default(client, outbox):
    h, _ = shopper(client, outbox)
    first = save_address(client, h)
    save_address(client, h, address="5 Market Street")
    assert (
        client.delete(f"/account/addresses/{first['id']}", headers=h).status_code == 204
    )
    left = client.get("/account/addresses", headers=h).json()
    assert len(left) == 1 and left[0]["is_default"] is True


def test_an_address_needs_a_real_pincode_and_fields(client, outbox):
    h, _ = shopper(client, outbox)
    for bad in (
        {"pincode": "5742"},
        {"pincode": "abcdef"},
        {"address": "x"},
        {"town": ""},
        {"recipient_name": "A"},
    ):
        r = client.post("/account/addresses", headers=h, json={**ADDRESS, **bad})
        assert r.status_code == 422, bad


def test_only_ten_addresses_can_be_saved(client, outbox):
    h, _ = shopper(client, outbox)
    for i in range(10):
        save_address(client, h, address=f"{i} Temple Road")
    r = client.post("/account/addresses", headers=h, json=ADDRESS)
    assert r.status_code == 400 and "10" in r.json()["detail"]


def test_nobody_can_see_or_change_another_shoppers_addresses(client, outbox):
    mine, _ = shopper(client, outbox)
    theirs, _ = shopper(client, outbox, name="Ravi Kumar")
    address = save_address(client, mine)

    assert client.get("/account/addresses", headers=theirs).json() == []
    url = f"/account/addresses/{address['id']}"
    assert client.put(url, headers=theirs, json=ADDRESS).status_code == 404
    assert client.delete(url, headers=theirs).status_code == 404
    assert len(client.get("/account/addresses", headers=mine).json()) == 1


def test_addresses_and_orders_need_a_customer_sign_in(client, people):
    for path in ("/account/addresses", "/account/orders"):
        assert client.get(path).status_code == 401
        assert client.get(path, headers=people["owner"]["h"]).status_code == 401


# ---------------------------------------------------------------------- orders
def test_an_order_to_a_saved_address_belongs_to_the_account(client, outbox, db):
    item = make_item(db, price="1500.00")
    h, email = shopper(client, outbox)
    address = save_address(client, h)

    r = order_with(client, h, item, qty=2, address_id=address["id"], note="Call first")
    assert r.status_code == 201, r.text
    order = r.json()
    assert Decimal(order["total"]) == Decimal("3000.00")
    assert order["status"] == "draft" and order["items"][0]["quantity"] == 2

    row = db.query(Order).filter(Order.order_number == order["order_number"]).one()
    assert row.account_id is not None and row.source == "web"
    assert row.delivery_address["pincode"] == "574201"
    assert row.delivery_address["address"] == "12 Temple Road"
    assert row.customer.phone == PHONE and row.customer.email == email

    mine = client.get("/account/orders", headers=h).json()
    assert [o["order_number"] for o in mine] == [order["order_number"]]
    one = client.get(f"/account/orders/{order['order_number']}", headers=h)
    assert one.status_code == 200 and one.json()["total"] == order["total"]


def test_name_and_phone_default_from_the_address_then_the_account(client, outbox, db):
    item = make_item(db)
    h, _ = shopper(client, outbox, name="Asha Rao")
    one_off = {k: v for k, v in ADDRESS.items() if k not in ("phone", "label")}

    no_phone = order_with(client, h, item, address=one_off)
    assert no_phone.status_code == 422 and "phone" in no_phone.json()["detail"]

    client.patch("/account/me", headers=h, json={"phone": f"+91 {ACCOUNT_PHONE}"})
    ok = order_with(client, h, item, address=one_off)  # account phone fills in
    assert ok.status_code == 201, ok.text
    row = db.query(Order).filter(Order.order_number == ok.json()["order_number"]).one()
    assert row.customer.name == "Asha Rao" and row.customer.phone.endswith(
        ACCOUNT_PHONE
    )


def test_a_shopper_only_sees_their_own_orders(client, outbox, db):
    item = make_item(db, stock=10)
    mine, _ = shopper(client, outbox)
    theirs, _ = shopper(client, outbox, name="Ravi Kumar")
    a1, a2 = save_address(client, mine), save_address(client, theirs)
    mine_order = order_with(client, mine, item, address_id=a1["id"]).json()
    their_order = order_with(client, theirs, item, address_id=a2["id"]).json()

    assert [
        o["order_number"] for o in client.get("/account/orders", headers=mine).json()
    ] == [mine_order["order_number"]]
    miss = client.get(f"/account/orders/{their_order['order_number']}", headers=mine)
    assert miss.status_code == 404
    assert client.get("/account/orders/ORD-NOPE", headers=mine).status_code == 404


def test_a_guest_order_with_the_same_phone_is_not_in_the_account(client, outbox, db):
    item = make_item(db, stock=10)
    h, _ = shopper(client, outbox)
    address = save_address(client, h)
    guest = client.post(
        "/public/checkout",
        json={
            "name": "Asha Rao",
            "phone": address["phone"],
            "address": "12 Temple Road",
            "town": "Puttur",
            "pincode": "574201",
            "items": [{"slug": item.slug, "quantity": 1}],
        },
    )
    assert guest.status_code == 201, guest.text
    assert client.get("/account/orders", headers=h).json() == []
    row = (
        db.query(Order).filter(Order.order_number == guest.json()["order_number"]).one()
    )
    assert row.account_id is None  # guest checkout is unchanged


def test_orders_come_newest_first_and_page(client, outbox, db):
    item = make_item(db, stock=20)
    h, _ = shopper(client, outbox)
    address = save_address(client, h)
    numbers = [
        order_with(client, h, item, address_id=address["id"]).json()["order_number"]
        for _ in range(3)
    ]
    listed = client.get("/account/orders", headers=h).json()
    assert [o["order_number"] for o in listed] == numbers[::-1]
    page = client.get("/account/orders?limit=1&offset=1", headers=h).json()
    assert [o["order_number"] for o in page] == [numbers[1]]


@pytest.mark.parametrize(
    "mutate,status",
    [
        ({"address_id": str(uuid.uuid4())}, 404),  # not a saved address
        ({"address": ADDRESS}, 422),  # both given
        ({"address_id": None}, 422),  # neither given
        ({"items": []}, 422),
        ({"items": [{"slug": "no-such-thing", "quantity": 1}]}, 400),
        ({"items": [{"slug": "x", "quantity": 0}]}, 422),
        ({"pay_option": "cash"}, 422),
    ],
)
def test_bad_orders_are_refused_and_create_nothing(client, outbox, db, mutate, status):
    item = make_item(db)
    h, _ = shopper(client, outbox)
    address = save_address(client, h)
    body = {
        "items": [{"slug": item.slug, "quantity": 1}],
        "address_id": address["id"],
        **mutate,
    }
    before = db.query(Order).count()
    r = client.post("/account/checkout", headers=h, json=body)
    assert r.status_code == status, r.text
    assert db.query(Order).count() == before


def test_the_same_rules_as_guest_checkout_apply(client, outbox, db, monkeypatch):
    stocked = make_item(db, stock=1)
    custom = make_item(db, name="Carved Door", stock=0, made_to_order=True)
    h, _ = shopper(client, outbox)
    address = save_address(client, h)

    short = order_with(client, h, stocked, qty=2, address_id=address["id"])
    assert short.status_code == 400 and "stock" in short.json()["detail"].lower()

    cod = order_with(
        client, h, custom, address_id=address["id"], pay_option="on_delivery"
    )
    assert cod.status_code == 400 and "made to order" in cod.json()["detail"]

    monkeypatch.setattr(settings, "DELIVERY_PINCODES", ["575001"])
    far = order_with(client, h, stocked, address_id=address["id"])
    assert far.status_code == 400 and "deliver" in far.json()["detail"]


def test_the_shopper_cannot_set_prices_or_the_account(client, outbox, db):
    item = make_item(db, price="1000.00")
    h, _ = shopper(client, outbox)
    other, _ = shopper(client, outbox, name="Ravi Kumar")
    address = save_address(client, h)
    r = client.post(
        "/account/checkout",
        headers=h,
        json={
            "items": [{"slug": item.slug, "quantity": 1, "unit_price": 1}],
            "address_id": address["id"],
            "estimated_amount": 1,
            "total": 1,
            "account_id": str(uuid.uuid4()),
            "status": "delivered",
        },
    )
    assert r.status_code == 201, r.text
    assert (
        Decimal(r.json()["total"]) == Decimal("1000.00")
        and r.json()["status"] == "draft"
    )
    assert client.get("/account/orders", headers=other).json() == []


def test_a_custom_piece_request_is_tied_to_the_account(client, outbox, db):
    h, _ = shopper(client, outbox)
    client.patch("/account/me", headers=h, json={"phone": PHONE})
    r = client.post(
        "/account/enquiry",
        headers=h,
        json={"what": "Temple door", "description": "Carved Ganesha, 7 feet, teak"},
    )
    assert r.status_code == 201, r.text
    assert r.json()["order_type"] == "custom"
    mine = client.get("/account/orders", headers=h).json()
    assert [o["order_number"] for o in mine] == [r.json()["order_number"]]


def test_checkout_without_a_sign_in_is_refused(client, db):
    item = make_item(db)
    r = client.post(
        "/account/checkout",
        json={"items": [{"slug": item.slug, "quantity": 1}], "address": ADDRESS},
    )
    assert r.status_code == 401


def test_deleting_the_account_removes_saved_addresses_but_not_the_orders(
    client, outbox, db
):
    item = make_item(db)
    h, _ = shopper(client, outbox)
    address = save_address(client, h)
    placed = order_with(client, h, item, address_id=address["id"]).json()
    account_id = client.get("/account/me", headers=h).json()["id"]

    assert client.delete("/account/me", headers=h).status_code == 204
    db.expire_all()
    assert (
        db.query(CustomerAddress)
        .filter(CustomerAddress.account_id == uuid.UUID(account_id))
        .count()
        == 0
    )
    # the order is a business record and stays, for the workshop to deliver
    assert (
        db.query(Order).filter(Order.order_number == placed["order_number"]).count()
        == 1
    )
    assert client.get("/account/orders", headers=h).status_code == 401
