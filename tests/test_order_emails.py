"""Order emails: received, confirmed, payment received. Brevo is faked, no network."""

import logging
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import httpx
import pytest
from sqlalchemy.exc import IntegrityError

from app.core import ratelimit
from app.core.config import settings
from app.models.customer import Customer
from app.models.order import Order
from app.models.order_email_log import OrderEmailLog
from app.models.order_item import OrderItem
from app.models.order_payment import OrderPayment
from app.services import email_service
from app.services import order_email_service as svc
from tests.test_account_orders import make_item, order_with, save_address, shopper
from tests.test_newsletter import FakeBrevo

STOREFRONT = "https://shop.example"
PHONE = "9876500022"
ADDRESS = "12 Temple Road, Puttur"


@pytest.fixture(autouse=True)
def brevo(monkeypatch):
    ratelimit.reset()
    monkeypatch.setattr(settings, "PUBLIC_RATE_LIMIT", 1000)
    monkeypatch.setattr(settings, "DELIVERY_PINCODES", [])
    monkeypatch.setattr(settings, "BREVO_API_KEY", "test-key")
    monkeypatch.setattr(settings, "BREVO_SENDER_EMAIL", "orders@vrikshastudios.com")
    monkeypatch.setattr(settings, "BREVO_SENDER_NAME", "Vriksha Studios")
    monkeypatch.setattr(settings, "STOREFRONT_URL", STOREFRONT)
    monkeypatch.setattr(settings, "ORDER_EMAILS_ENABLED", True)
    monkeypatch.setattr(settings, "PAY_UPI_ID", "vriksha@okbank")
    monkeypatch.setattr(settings, "PAY_UPI_NAME", "Vriksha Studios")
    fake = FakeBrevo()
    monkeypatch.setattr(httpx, "request", fake)
    yield fake
    ratelimit.reset()


def new_email():
    return f"shopper_{uuid.uuid4().hex[:10]}@example.com"


def checkout(client, item, email=None, qty=1, **over):
    body = {
        "name": "Meena",
        "phone": PHONE,
        "address": ADDRESS,
        "town": "Puttur",
        "pincode": "574201",
        "items": [{"slug": item.slug, "quantity": qty}],
        **over,
    }
    if email:
        body["email"] = email
    r = client.post("/public/checkout", json=body)
    assert r.status_code == 201, r.text
    return r.json()["order_number"]


def order_row(db, number):
    db.expire_all()
    return db.query(Order).filter(Order.order_number == number).one()


def log_rows(db, number):
    order = order_row(db, number)
    return (
        db.query(OrderEmailLog)
        .filter(OrderEmailLog.order_id == order.id)
        .order_by(OrderEmailLog.created_at)
        .all()
    )


def sent(brevo):
    return [c["json"] for c in brevo.to("/smtp/email")]


def staff_order(owner, db, email="customer@example.com"):
    """An order staff create in the app, for a customer record that has an email."""
    customer = Customer(name="Walk In", phone="9811111111", email=email)
    db.add(customer)
    db.commit()
    item = make_item(db)
    r = owner.post(
        "/orders/",
        json={
            "customer_id": str(customer.id),
            "order_type": "standard",
            "estimated_amount": 1000,
            "items": [{"item_id": str(item.id), "quantity": 1, "unit_price": 1000}],
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def set_status(owner, order_id, status):
    return owner.patch(f"/orders/{order_id}/status", json={"status": status})


def pay(owner, order_id, amount, **over):
    return owner.post(
        f"/orders/{order_id}/payments", json={"amount": amount, "method": "upi", **over}
    )


def direct_order(db, email, number=None):
    """An order row straight in the database (for the thread tests)."""
    order = Order(
        order_number=number or f"T-{uuid.uuid4().hex[:10]}",
        status="draft",
        source="web",
        notify_email=email,
        estimated_amount=Decimal("500"),
        items=[OrderItem(item_name="Stool", quantity=1, unit_price=Decimal("500"))],
    )
    db.add(order)
    db.commit()
    return order.id


# ------------------------------------------------------------------ order received
def test_checkout_with_an_email_sends_order_received(client, db, brevo):
    item = make_item(db, price="104500.00", stock=5)
    email = new_email()
    number = checkout(client, item, email, qty=1)

    (mail,) = sent(brevo)
    assert mail["to"] == [{"email": email}]
    assert mail["sender"] == {
        "email": "orders@vrikshastudios.com",
        "name": "Vriksha Studios",
    }
    assert number in mail["subject"] and "Vriksha Studios" in mail["subject"]
    text, page = mail["textContent"], mail["htmlContent"]
    link = f"{STOREFRONT}/track?order={number}"
    assert link in text and link in page
    assert f"{item.name} x 1" in text
    assert "Rs. 1,04,500" in text and "Rs. 1,04,500" in page
    assert "call or WhatsApp" in text
    assert "If you did not place this order" in text
    # the call was short, and the email says nothing about marketing or the newsletter
    assert brevo.calls[0]["timeout"] <= 10
    assert "newsletter" not in text.lower()
    # no phone number and no street address in the email
    for body in (text, page, mail["subject"]):
        assert PHONE not in body and "Temple Road" not in body
    (row,) = log_rows(db, number)
    assert (row.kind, row.status, row.to_email) == ("order_received", "sent", email)
    assert order_row(db, number).notify_email == email


def test_the_address_is_stored_and_used_lower_case(client, db, brevo):
    item = make_item(db)
    email = new_email()
    number = checkout(client, item, email.upper().replace("EXAMPLE.COM", "Example.com"))
    assert order_row(db, number).notify_email == email
    assert sent(brevo)[0]["to"] == [{"email": email}]


def test_no_email_given_means_nothing_sent_or_recorded(client, db, brevo):
    number = checkout(client, make_item(db))
    assert brevo.calls == []
    assert log_rows(db, number) == []
    assert order_row(db, number).notify_email is None


def test_existing_customer_email_is_not_used(client, db, brevo):
    # a customer record that already has an email, ordering again without typing one
    db.add(Customer(name="Old", phone=PHONE, email="old@example.com"))
    db.commit()
    number = checkout(client, make_item(db))
    assert brevo.calls == []
    assert order_row(db, number).notify_email is None


@pytest.fixture
def outbox(monkeypatch):
    """Sign-in codes go here (not through Brevo)."""
    box = []
    monkeypatch.setattr(
        email_service, "send", lambda to, subject, body: box.append((to, subject, body))
    )
    return box


def test_a_signed_in_shopper_is_emailed_at_their_account_address(
    client, db, brevo, outbox
):
    h, account_email = shopper(client, outbox)
    # own phone number: customers are found by phone and shared by the whole test run
    phone = "9" + str(uuid.uuid4().int)[:9]
    address = save_address(client, h, phone=phone)
    r = order_with(client, h, make_item(db), address_id=address["id"])
    assert r.status_code == 201, r.text
    (mail,) = sent(brevo)
    assert mail["to"] == [{"email": account_email}]
    assert r.json()["order_number"] in mail["subject"]


def test_enquiry_gets_no_email(client, db, brevo):
    r = client.post(
        "/public/enquiry",
        json={
            "name": "Meena",
            "phone": PHONE,
            "what": "A carved door",
            "description": "Teak, 7 ft",
        },
    )
    assert r.status_code == 201, r.text
    assert brevo.calls == []


# ------------------------------------------------------- staff orders are never emailed
def test_staff_created_orders_never_send_email(auth_client, db, brevo):
    order = staff_order(auth_client, db)
    assert order_row(db, order["order_number"]).notify_email is None
    assert set_status(auth_client, order["id"], "confirmed").status_code == 200
    assert pay(auth_client, order["id"], 400).status_code == 201
    assert brevo.calls == []
    assert log_rows(db, order["order_number"]) == []


# ----------------------------------------------------------------- order confirmed
def web_order(client, db, price="2000.00", qty=1):
    email = new_email()
    number = checkout(client, make_item(db, price=price, stock=9), email, qty=qty)
    return number, email, order_row(db, number).id


def test_confirming_sends_the_confirmation_with_upi_instructions(
    auth_client, db, brevo
):
    number, email, oid = web_order(auth_client, db, price="2000.00")
    assert pay(auth_client, oid, 500).status_code == 201  # the advance, taken by phone
    brevo.calls.clear()

    assert set_status(auth_client, oid, "confirmed").status_code == 200
    (mail,) = sent(brevo)
    assert mail["to"] == [{"email": email}]
    assert number in mail["subject"] and "confirmed" in mail["subject"]
    text = mail["textContent"]
    assert "Total: Rs. 2,000" in text
    assert "Advance paid so far: Rs. 500" in text
    assert "Balance due: Rs. 1,500" in text
    assert "vriksha@okbank" in text and "Vriksha Studios" in text
    assert f"write your order number {number}" in text
    assert "upi://" not in text and "upi://" not in mail["htmlContent"]
    assert f"{STOREFRONT}/track?order={number}" in mail["htmlContent"]


def test_nothing_about_paying_when_nothing_is_due(auth_client, db, brevo):
    number, _, oid = web_order(auth_client, db, price="2000.00")
    assert pay(auth_client, oid, 2000).status_code == 201
    brevo.calls.clear()
    set_status(auth_client, oid, "confirmed")
    (mail,) = sent(brevo)
    assert "Balance due: Rs. 0" in mail["textContent"]
    assert "vriksha@okbank" not in mail["textContent"]
    assert "UPI" not in mail["textContent"] and "UPI" not in mail["htmlContent"]


def test_confirmed_without_a_upi_id_offers_cash(auth_client, db, brevo, monkeypatch):
    monkeypatch.setattr(settings, "PAY_UPI_ID", "")
    _, _, oid = web_order(auth_client, db)
    brevo.calls.clear()
    set_status(auth_client, oid, "confirmed")
    text = sent(brevo)[0]["textContent"]
    assert "in cash at the shop" in text and "UPI id" not in text


def test_a_repeated_status_call_sends_nothing_more(auth_client, db, brevo):
    number, _, oid = web_order(auth_client, db)
    assert set_status(auth_client, oid, "confirmed").status_code == 200
    assert set_status(auth_client, oid, "confirmed").status_code == 400  # not allowed
    assert set_status(auth_client, oid, "in_production").status_code == 200
    kinds = [r.kind for r in log_rows(db, number)]
    assert kinds.count("order_confirmed") == 1
    assert len(sent(brevo)) == 2  # received + confirmed, nothing for production


def test_no_email_for_the_other_statuses(auth_client, db, brevo):
    number, _, oid = web_order(auth_client, db)
    for status in ("confirmed", "in_production", "ready", "delivered"):
        assert set_status(auth_client, oid, status).status_code == 200
    assert [r.kind for r in log_rows(db, number)] == [
        "order_received",
        "order_confirmed",
    ]
    number2, _, oid2 = web_order(auth_client, db)
    set_status(auth_client, oid2, "cancelled")
    assert [r.kind for r in log_rows(db, number2)] == ["order_received"]


# ---------------------------------------------------------------- payment received
def test_each_payment_gets_one_email(auth_client, db, brevo):
    number, email, oid = web_order(auth_client, db, price="2000.00")
    set_status(auth_client, oid, "confirmed")
    brevo.calls.clear()

    assert pay(auth_client, oid, 500).status_code == 201
    (first,) = sent(brevo)
    assert first["to"] == [{"email": email}]
    assert number in first["subject"] and "Payment received" in first["subject"]
    assert "We received Rs. 500 " in first["textContent"]
    assert "balance still due is Rs. 1,500" in first["textContent"]
    assert f"{STOREFRONT}/track?order={number}" in first["htmlContent"]

    # a second payment is a new payment, so a new email; the last one clears the balance
    assert pay(auth_client, oid, 1500).status_code == 201
    second = sent(brevo)[1]
    assert "We received Rs. 1,500 " in second["textContent"]
    assert "Nothing more is due" in second["textContent"]
    assert len(sent(brevo)) == 2


def test_the_same_payment_is_never_emailed_twice(auth_client, db, brevo):
    number, _, oid = web_order(auth_client, db)
    pay(auth_client, oid, 300)
    (payment_id,) = [r.payment_id for r in log_rows(db, number) if r.payment_id]
    before = len(sent(brevo))
    svc.send(oid, "payment_received", payment_id)  # e.g. a retried task
    svc.send(oid, "payment_received", payment_id)
    assert len(sent(brevo)) == before


def test_a_rejected_payment_sends_nothing(auth_client, db, brevo):
    _, _, oid = web_order(auth_client, db, price="2000.00")
    brevo.calls.clear()
    assert pay(auth_client, oid, 999999).status_code == 400
    assert brevo.calls == []


def test_a_voided_payment_is_not_announced(db, brevo):
    oid = direct_order(db, new_email())
    p = OrderPayment(
        order_id=oid, amount=Decimal("300"), voided_at=datetime.now(timezone.utc)
    )
    db.add(p)
    db.commit()
    svc.send(oid, "payment_received", p.id)
    assert brevo.calls == []
    db.expire_all()
    (row,) = db.query(OrderEmailLog).filter_by(order_id=oid).all()
    assert row.status == "skipped" and "voided" in row.error


# ------------------------------------------------------------------------ the limit
def test_at_most_three_order_received_emails_per_address_per_day(client, db, brevo):
    email = new_email()
    numbers = [checkout(client, make_item(db), email) for _ in range(5)]  # all 201
    assert len(sent(brevo)) == 3
    statuses = [log_rows(db, n)[0].status for n in numbers]
    assert statuses == ["sent", "sent", "sent", "skipped", "skipped"]
    assert "limit" in log_rows(db, numbers[3])[0].error
    # the orders exist, and another address is unaffected
    assert all(order_row(db, n) for n in numbers)
    checkout(client, make_item(db), new_email())
    assert len(sent(brevo)) == 4


def test_the_limit_ignores_letter_case(client, db, brevo):
    email = new_email()
    for _ in range(3):
        checkout(client, make_item(db), email)
    checkout(client, make_item(db), email.upper().replace("EXAMPLE.COM", "example.com"))
    assert len(sent(brevo)) == 3


def test_old_emails_fall_out_of_the_window(client, db, brevo):
    email = new_email()
    for _ in range(3):
        checkout(client, make_item(db), email)
    db.query(OrderEmailLog).filter(OrderEmailLog.to_email == email).update(
        {"created_at": datetime.now(timezone.utc) - timedelta(hours=25)}
    )
    db.commit()
    checkout(client, make_item(db), email)
    assert len(sent(brevo)) == 4


def test_the_limit_holds_when_checkouts_race(db, brevo):
    email = new_email()
    ids = [direct_order(db, email) for _ in range(8)]
    go = threading.Barrier(len(ids))

    def run(oid):
        go.wait()
        svc.send(oid, "order_received")

    with ThreadPoolExecutor(len(ids)) as pool:
        list(pool.map(run, ids))
    assert len(sent(brevo)) == svc.CAP_PER_ADDRESS
    db.expire_all()
    rows = db.query(OrderEmailLog).filter(OrderEmailLog.to_email == email).all()
    assert sorted(r.status for r in rows) == ["sent"] * 3 + ["skipped"] * 5


# ------------------------------------------------------------------------ once only
def test_racing_requests_send_one_email(db, brevo):
    oid = direct_order(db, new_email())
    go = threading.Barrier(8)

    def run(_):
        go.wait()
        svc.send(oid, "order_confirmed")

    with ThreadPoolExecutor(8) as pool:
        list(pool.map(run, range(8)))
    assert len(sent(brevo)) == 1
    db.expire_all()
    assert db.query(OrderEmailLog).filter_by(order_id=oid).count() == 1


def test_the_database_itself_refuses_a_second_claim(db):
    oid = direct_order(db, new_email())
    pay_id = uuid.uuid4()
    db.add(OrderPayment(id=pay_id, order_id=oid, amount=Decimal("10")))
    db.commit()

    def add(kind, payment_id=None):
        db.add(
            OrderEmailLog(
                order_id=oid,
                kind=kind,
                payment_id=payment_id,
                to_email="a@example.com",
                status="sent",
            )
        )
        db.commit()

    add("order_confirmed")
    add("payment_received", pay_id)
    for again in (("order_confirmed", None), ("payment_received", pay_id)):
        with pytest.raises(IntegrityError):
            add(*again)
        db.rollback()
    add("payment_received", None)  # a different (order, kind) shape is its own slot


# ------------------------------------------------------------ not set up, switched off
@pytest.mark.parametrize(
    "setting", ["BREVO_API_KEY", "BREVO_SENDER_EMAIL", "STOREFRONT_URL"]
)
def test_missing_settings_skip_and_record(client, db, brevo, monkeypatch, setting):
    monkeypatch.setattr(settings, setting, "")
    number = checkout(client, make_item(db), new_email())
    assert brevo.calls == []
    (row,) = log_rows(db, number)
    assert row.status == "skipped" and "not set up" in row.error


def test_in_dev_the_tracking_link_is_logged(client, db, brevo, monkeypatch, caplog):
    monkeypatch.setattr(settings, "BREVO_API_KEY", "")
    monkeypatch.setattr(settings, "STOREFRONT_URL", "")
    monkeypatch.setattr(settings, "ENV", "dev")
    monkeypatch.setattr(settings, "IS_PROD", False)
    with caplog.at_level(logging.INFO):
        number = checkout(client, make_item(db), new_email())
    assert f"http://localhost:3000/track?order={number}" in caplog.text
    assert brevo.calls == []


def test_the_kill_switch(client, db, auth_client, brevo, monkeypatch):
    monkeypatch.setattr(settings, "ORDER_EMAILS_ENABLED", False)
    number = checkout(client, make_item(db), new_email())
    oid = order_row(db, number).id
    assert set_status(auth_client, oid, "confirmed").status_code == 200
    assert pay(auth_client, oid, 100).status_code == 201
    assert brevo.calls == []
    rows = log_rows(db, number)
    assert {r.status for r in rows} == {"skipped"} and len(rows) == 3
    assert "switched off" in rows[0].error


# ------------------------------------------------------------------ Brevo goes wrong
@pytest.mark.parametrize("how", ["http_500", "timeout"])
def test_a_brevo_failure_never_breaks_the_real_action(auth_client, db, brevo, how):
    if how == "http_500":
        brevo.status = 500
    else:
        brevo.network_error = True
    number, _, oid = web_order(auth_client, db, price="2000.00")  # checkout is 201
    assert set_status(auth_client, oid, "confirmed").status_code == 200
    assert pay(auth_client, oid, 500).status_code == 201

    rows = log_rows(db, number)
    assert [r.kind for r in rows] == [
        "order_received",
        "order_confirmed",
        "payment_received",
    ]
    assert {r.status for r in rows} == {"failed"}
    assert all(r.error and "test-key" not in r.error for r in rows)
    assert order_row(db, number).status == "confirmed"
    assert order_row(db, number).advance_paid == Decimal("500")


def test_a_failed_email_is_not_retried(auth_client, db, brevo):
    brevo.status = 500
    number, _, oid = web_order(auth_client, db)
    calls = len(brevo.calls)
    brevo.status = None
    svc.send(oid, "order_received")
    assert len(brevo.calls) == calls
    assert log_rows(db, number)[0].status == "failed"


def test_a_bug_in_a_template_is_recorded_not_raised(client, db, brevo, monkeypatch):
    def boom(order, payment_id, db):
        raise RuntimeError("template bug")

    monkeypatch.setitem(svc._KINDS, "order_received", boom)
    number = checkout(client, make_item(db), new_email())
    (row,) = log_rows(db, number)
    assert (row.status, row.error) == ("failed", "RuntimeError")


def test_send_swallows_anything(monkeypatch, caplog):
    def explode(*a):
        raise RuntimeError("db down")

    monkeypatch.setattr(svc, "_send", explode)
    svc.send(uuid.uuid4(), "order_received")  # must not raise
    assert "crashed" in caplog.text


# ------------------------------------------------------------------------- escaping
def test_customer_supplied_text_is_escaped_in_html(client, db, brevo):
    evil_item = make_item(db)
    evil_item.name = "<script>alert(1)</script> Chair"  # the slug stays plain
    db.commit()
    evil_name = '<img src=x onerror="alert(2)">'
    number = checkout(client, evil_item, new_email(), name=evil_name)
    (mail,) = sent(brevo)
    page, text = mail["htmlContent"], mail["textContent"]
    assert "<script>" not in page and "&lt;script&gt;alert(1)&lt;/script&gt;" in page
    assert "<img" not in page and evil_name not in page
    assert "<script>alert(1)</script> Chair" in text  # plain text needs no escaping
    assert number in mail["subject"]


def test_renderer_escapes_every_block_kind():
    nasty = '<i>"&'
    blocks = [
        ("p", nasty),
        ("lines", [nasty]),
        ("facts", [(nasty, nasty)]),
        ("link", (nasty, "https://x.example/?a=1&b=<2>")),
    ]
    page = svc._as_html(blocks)
    assert "<i>" not in page and "&lt;i&gt;" in page
    assert "?a=1&amp;b=&lt;2&gt;" in page


def test_rupees_use_indian_grouping():
    assert svc.rupees(104500) == "Rs. 1,04,500"
    assert svc.rupees(Decimal("12345678.50")) == "Rs. 1,23,45,678.50"
    assert svc.rupees(0) == "Rs. 0"
    assert svc.rupees(999) == "Rs. 999"
