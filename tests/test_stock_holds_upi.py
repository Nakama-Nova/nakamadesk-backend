"""
Cash and UPI, no payment gateway: staff confirm an order by phone, which holds its
stock so it cannot be promised twice; the shopper then pays cash or UPI, and staff
record it. The public order view carries a UPI link once the order is confirmed.
"""

import random
import urllib.parse
import uuid
from decimal import Decimal

import pytest

from app.core import ratelimit
from app.core.config import settings
from app.models.order import Order
from app.models.stock_reservation import StockReservation
from tests.test_account_orders import make_item

PHONE = "9" + "".join(random.choice("0123456789") for _ in range(9))


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    ratelimit.reset()
    monkeypatch.setattr(settings, "DELIVERY_PINCODES", [])
    monkeypatch.setattr(settings, "PUBLIC_RATE_LIMIT", 500)
    monkeypatch.setattr(settings, "PAY_UPI_ID", "vriksha@okbank")
    monkeypatch.setattr(settings, "PAY_UPI_NAME", "Vriksha Studios")
    monkeypatch.setattr(settings, "PAY_PHONE", "+91 98765 00000")
    yield
    ratelimit.reset()


def guest_order(client, item, qty=1, phone=PHONE):
    r = client.post(
        "/public/checkout",
        json={
            "name": "Meena",
            "phone": phone,
            "address": "12 Temple Road",
            "town": "Puttur",
            "pincode": "574201",
            "items": [{"slug": item.slug, "quantity": qty}],
        },
    )
    assert r.status_code == 201, r.text
    return r.json()["order_number"]


def order_row(db, number):
    db.expire_all()
    return db.query(Order).filter(Order.order_number == number).one()


def holds(db, number):
    db.expire_all()
    order = order_row(db, number)
    return (
        db.query(StockReservation)
        .filter(
            StockReservation.order_id == order.id,
            StockReservation.released_at.is_(None),
        )
        .all()
    )


def set_status(owner, db, number, status):
    r = owner.patch(
        f"/orders/{order_row(db, number).id}/status", json={"status": status}
    )
    assert r.status_code == 200, r.text
    return r


# ----------------------------------------------------------------------- holds
def test_confirming_an_order_holds_its_stock_without_moving_it(client, client_as, db):
    item = make_item(db, stock=5)
    number = guest_order(client, item, qty=2)
    assert holds(db, number) == []  # an unconfirmed order promises nothing

    owner, _ = client_as("owner")
    set_status(owner, db, number, "confirmed")
    [held] = holds(db, number)
    assert (held.item_id, held.quantity) == (item.id, 2)
    db.refresh(item)
    assert item.current_stock == 5  # the shelf count only changes when it is billed


def test_made_to_order_pieces_are_not_held(client, client_as, db):
    item = make_item(db, name="Carved Door", stock=0, made_to_order=True)
    number = guest_order(client, item)
    owner, _ = client_as("owner")
    set_status(owner, db, number, "confirmed")
    assert holds(db, number) == []


def test_held_units_cannot_be_promised_to_another_shopper(client, client_as, db):
    item = make_item(db, stock=1)
    number = guest_order(client, item)
    owner, _ = client_as("owner")
    set_status(owner, db, number, "confirmed")

    late = client.post(
        "/public/checkout",
        json={
            "name": "Ravi",
            "phone": "9" + "1" * 9,
            "address": "5 Market Street",
            "town": "Puttur",
            "pincode": "574201",
            "items": [{"slug": item.slug, "quantity": 1}],
        },
    )
    assert late.status_code == 400 and "stock" in late.json()["detail"].lower()


def test_a_counter_sale_cannot_take_held_units(client, client_as, db):
    item = make_item(db, stock=3)
    number = guest_order(client, item, qty=2)
    owner, _ = client_as("owner")
    set_status(owner, db, number, "confirmed")

    too_many = owner.post(
        "/sales/", json={"items": [{"item_id": str(item.id), "quantity": 2}]}
    )
    assert too_many.status_code == 400 and "Available: 1" in too_many.json()["detail"]
    one = owner.post(
        "/sales/", json={"items": [{"item_id": str(item.id), "quantity": 1}]}
    )
    assert one.status_code == 200, one.text


def test_confirming_with_too_little_on_the_shelf_holds_what_is_there(
    client, client_as, db
):
    item = make_item(db, stock=2)
    number = guest_order(client, item, qty=2)
    owner, _ = client_as("owner")
    sold = owner.post(
        "/sales/", json={"items": [{"item_id": str(item.id), "quantity": 1}]}
    )
    assert sold.status_code == 200
    set_status(owner, db, number, "confirmed")  # not refused
    [held] = holds(db, number)
    assert held.quantity == 1


def test_two_orders_for_the_last_unit_only_the_first_confirmed_holds_it(
    client, client_as, db
):
    item = make_item(db, stock=1)
    first = guest_order(client, item)
    second = guest_order(client, item, phone="9" + "2" * 9)
    owner, _ = client_as("owner")
    set_status(owner, db, first, "confirmed")
    set_status(owner, db, second, "confirmed")
    assert len(holds(db, first)) == 1 and holds(db, second) == []


@pytest.mark.parametrize("end", ["cancelled", "delivered"])
def test_cancelling_or_delivering_lets_the_stock_go(client, client_as, db, end):
    item = make_item(db, stock=2)
    number = guest_order(client, item)
    owner, _ = client_as("owner")
    set_status(owner, db, number, "confirmed")
    assert len(holds(db, number)) == 1
    if end == "delivered":
        set_status(owner, db, number, "in_production")
        set_status(owner, db, number, "ready")
    set_status(owner, db, number, end)
    assert holds(db, number) == []


def test_billing_a_held_order_uses_its_own_hold_and_releases_it(client, client_as, db):
    item = make_item(db, stock=3, price="1000.00")
    number = guest_order(client, item, qty=2)
    owner, _ = client_as("owner")
    set_status(owner, db, number, "confirmed")
    sold = owner.post(
        "/sales/", json={"items": [{"item_id": str(item.id), "quantity": 1}]}
    )
    assert sold.status_code == 200  # 3 on the shelf, 2 held: one is free

    billed = owner.post(f"/orders/{order_row(db, number).id}/invoice")
    assert billed.status_code == 201, billed.text  # its own two are not blocked
    db.refresh(item)
    assert item.current_stock == 0
    assert holds(db, number) == []


# -------------------------------------------------------------- cash and UPI
def test_payment_info_says_how_to_pay(client):
    r = client.get("/public/payment-info")
    assert r.status_code == 200
    body = r.json()
    assert body["accepts"] == ["cash", "upi"]
    assert (
        body["upi_id"] == "vriksha@okbank" and body["payee_name"] == "Vriksha Studios"
    )
    assert body["phone"] == "+91 98765 00000" and "confirm" in body["steps"].lower()


def test_payment_info_without_a_upi_id_offers_cash_and_a_phone_number_only(
    client, monkeypatch
):
    monkeypatch.setattr(settings, "PAY_UPI_ID", "")
    body = client.get("/public/payment-info").json()
    assert body["upi_id"] is None and body["payee_name"] is None
    assert body["accepts"] == [
        "cash",
        "upi",
    ]  # UPI to the shop's number is still possible


def track(client, number, phone=PHONE):
    r = client.get(f"/public/orders/{number}", params={"phone": phone})
    assert r.status_code == 200, r.text
    return r.json()


def test_the_upi_link_appears_only_once_confirmed_and_while_money_is_due(
    client, client_as, db
):
    item = make_item(db, stock=5, price="1000.00")
    number = guest_order(client, item, qty=2)
    assert track(client, number)["upi_link"] is None  # not confirmed yet

    owner, _ = client_as("owner")
    set_status(owner, db, number, "confirmed")
    link = track(client, number)["upi_link"]
    parts = urllib.parse.urlparse(link)
    query = urllib.parse.parse_qs(parts.query)
    assert parts.scheme == "upi" and parts.netloc == "pay"
    assert query["pa"] == ["vriksha@okbank"] and query["pn"] == ["Vriksha Studios"]
    assert query["tn"] == [number] and query["cu"] == ["INR"] and "am" not in query

    order_id = order_row(db, number).id
    cash = owner.post(
        f"/orders/{order_id}/payments", json={"amount": "500", "method": "cash"}
    )
    assert cash.status_code == 201, cash.text
    mid = track(client, number)
    assert Decimal(mid["balance_due"]) == 1500 and mid["upi_link"] is not None

    upi = owner.post(
        f"/orders/{order_id}/payments",
        json={"amount": "1500", "method": "upi", "reference": "UPI123456"},
    )
    assert upi.status_code == 201, upi.text
    done = track(client, number)
    assert Decimal(done["balance_due"]) == 0 and done["upi_link"] is None


def test_no_link_without_a_upi_id_and_none_on_a_cancelled_order(
    client, client_as, db, monkeypatch
):
    item = make_item(db, stock=5)
    number = guest_order(client, item)
    owner, _ = client_as("owner")
    set_status(owner, db, number, "confirmed")
    monkeypatch.setattr(settings, "PAY_UPI_ID", "")
    assert track(client, number)["upi_link"] is None
    monkeypatch.setattr(settings, "PAY_UPI_ID", "vriksha@okbank")
    assert track(client, number)["upi_link"] is not None
    set_status(owner, db, number, "cancelled")
    assert track(client, number)["upi_link"] is None


def test_a_signed_in_shopper_sees_the_link_on_their_own_order(client, client_as, db):
    import re

    from app.services import email_service

    sent = []
    original = email_service.send
    email_service.send = lambda to, subject, body: sent.append(body)
    try:
        email = f"shopper_{uuid.uuid4().hex[:10]}@example.com"
        client.post("/account/request-code", json={"email": email})
        code = re.search(r"\b(\d{6})\b", sent[-1]).group(1)
        token = client.post(
            "/account/verify",
            json={
                "email": email,
                "code": code,
                "name": "Asha Rao",
                "accept_terms": True,
            },
        ).json()["access_token"]
    finally:
        email_service.send = original
    h = {"Authorization": f"Bearer {token}"}

    item = make_item(db, stock=5, price="1000.00")
    placed = client.post(
        "/account/checkout",
        headers=h,
        json={
            "items": [{"slug": item.slug, "quantity": 1}],
            "name": "Asha Rao",
            "phone": "9" + "3" * 9,
            "address": {
                "recipient_name": "Asha Rao",
                "address": "12 Temple Road",
                "town": "Puttur",
                "pincode": "574201",
            },
        },
    )
    assert placed.status_code == 201, placed.text
    number = placed.json()["order_number"]
    assert placed.json()["upi_link"] is None

    owner, _ = client_as("owner")
    set_status(owner, db, number, "confirmed")
    mine = client.get(f"/account/orders/{number}", headers=h).json()
    assert mine["upi_link"].startswith("upi://pay?")
