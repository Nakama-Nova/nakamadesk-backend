"""
Paying an order online with Razorpay (test mode): starting a payment, setting stock
aside while the shopper pays, checking Razorpay's signatures, crediting the order
exactly once however many times it is told, and letting go of the stock afterwards.
Razorpay itself is never called: only its create-order call is replaced.
"""

import hashlib
import hmac
import json
import uuid
from datetime import timedelta
from decimal import Decimal

import pytest

from app.core import ratelimit
from app.core.config import settings
from app.models.order import Order
from app.models.order_payment import OrderPayment
from app.models.payment_intent import PaymentIntent
from app.models.stock_reservation import StockReservation
from app.services import email_service, razorpay_client
from tests.test_account_orders import make_item, order_with, save_address, shopper


@pytest.fixture
def outbox(monkeypatch):
    """Sign-in emails the app tries to send (the shopper helpers read codes from it)."""
    sent = []
    monkeypatch.setattr(
        email_service,
        "send",
        lambda to, subject, body: sent.append((to, subject, body)),
    )
    return sent


KEY_SECRET = "test_key_secret_123"
WEBHOOK_SECRET = "test_webhook_secret_456"


@pytest.fixture(autouse=True)
def razorpay(monkeypatch):
    """Razorpay set up with test keys; create_order records its calls and fakes an id."""
    ratelimit.reset()
    monkeypatch.setattr(settings, "RAZORPAY_KEY_ID", "rzp_test_abc123")
    monkeypatch.setattr(settings, "RAZORPAY_KEY_SECRET", KEY_SECRET)
    monkeypatch.setattr(settings, "RAZORPAY_WEBHOOK_SECRET", WEBHOOK_SECRET)
    monkeypatch.setattr(settings, "DELIVERY_PINCODES", [])
    monkeypatch.setattr(settings, "PUBLIC_RATE_LIMIT", 500)
    calls = []

    def fake_create_order(amount_paise, receipt, notes):
        calls.append({"amount": amount_paise, "receipt": receipt, "notes": notes})
        return {"id": f"order_{uuid.uuid4().hex[:14]}"}

    monkeypatch.setattr(razorpay_client, "create_order", fake_create_order)
    yield calls
    ratelimit.reset()


def checkout_sig(order_id, payment_id, secret=KEY_SECRET):
    return hmac.new(
        secret.encode(), f"{order_id}|{payment_id}".encode(), hashlib.sha256
    ).hexdigest()


def pay_id():
    return f"pay_{uuid.uuid4().hex[:14]}"


def webhook(client, event, rzp_order, payment, paise, secret=WEBHOOK_SECRET, raw=None):
    body = (
        raw
        or json.dumps(
            {
                "event": event,
                "payload": {
                    "payment": {
                        "entity": {
                            "id": payment,
                            "order_id": rzp_order,
                            "amount": paise,
                            "currency": "INR",
                            "status": (
                                "captured" if event == "payment.captured" else "failed"
                            ),
                        }
                    }
                },
            }
        ).encode()
    )
    sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return client.post(
        "/payments/razorpay/webhook",
        content=body,
        headers={"X-Razorpay-Signature": sig, "Content-Type": "application/json"},
    )


def placed(client, outbox, db, item, qty=2, **kw):
    """A signed-in shopper with a draft order for `qty` of the item: (headers, number)."""
    h, _ = shopper(client, outbox)
    address = save_address(client, h)
    r = order_with(client, h, item, qty=qty, address_id=address["id"], **kw)
    assert r.status_code == 201, r.text
    return h, r.json()["order_number"]


def row(db, number):
    db.expire_all()
    return db.query(Order).filter(Order.order_number == number).one()


def payments_of(db, order):
    db.expire_all()
    return (
        db.query(OrderPayment)
        .filter(OrderPayment.order_id == order.id, OrderPayment.method == "razorpay")
        .all()
    )


def start(client, h, number, **body):
    return client.post(f"/account/orders/{number}/pay", headers=h, json=body)


def reservations(db, order):
    db.expire_all()
    return (
        db.query(StockReservation).filter(StockReservation.order_id == order.id).all()
    )


# ---------------------------------------------------------------------- set up
def test_unset_razorpay_answers_503_everywhere(client, outbox, db, monkeypatch):
    item = make_item(db)
    h, number = placed(client, outbox, db, item)
    monkeypatch.setattr(settings, "RAZORPAY_KEY_ID", "")
    monkeypatch.setattr(settings, "RAZORPAY_KEY_SECRET", "")
    monkeypatch.setattr(settings, "RAZORPAY_WEBHOOK_SECRET", "")
    started = start(client, h, number)
    assert started.status_code == 503 and "not set up" in started.json()["detail"]
    assert reservations(db, row(db, number)) == []
    assert (
        webhook(client, "payment.captured", "order_x", "pay_x", 100).status_code == 503
    )


# -------------------------------------------------------------- starting a payment
def test_a_payment_asks_for_the_balance_and_sets_the_stock_aside(
    client, outbox, db, razorpay
):
    item = make_item(db, stock=3, price="1000.00")
    h, number = placed(client, outbox, db, item, qty=2)

    r = start(client, h, number)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["amount_paise"] == 200000 and body["currency"] == "INR"
    assert body["key_id"] == "rzp_test_abc123" and body["mode"] == "test"
    assert body["razorpay_order_id"].startswith("order_") and body["hold_expires_at"]
    assert "secret" not in json.dumps(body).lower()
    assert razorpay == [
        {"amount": 200000, "receipt": number, "notes": {"order_number": number}}
    ]

    order = row(db, number)
    held = reservations(db, order)
    assert [(r.item_id, r.quantity) for r in held] == [(item.id, 2)]
    assert held[0].expires_at is not None and held[0].released_at is None
    # stock itself is untouched until the order is billed
    db.refresh(item)
    assert item.current_stock == 3
    intent = db.query(PaymentIntent).filter(PaymentIntent.order_id == order.id).one()
    assert intent.status == "created" and intent.amount == Decimal("2000.00")
    assert payments_of(db, order) == []  # nothing is credited yet


def test_an_advance_can_be_paid_but_not_more_than_is_due(client, outbox, db):
    item = make_item(db, price="1000.00")
    h, number = placed(client, outbox, db, item, qty=2)
    assert start(client, h, number, amount="500").json()["amount_paise"] == 50000
    assert start(client, h, number, amount="2000.01").status_code == 400
    assert start(client, h, number, amount="0.50").status_code == 400  # under Rs 1
    for bad in ("0", "-5", "abc"):
        assert start(client, h, number, amount=bad).status_code == 422


def test_a_custom_request_with_no_price_yet_has_nothing_to_pay(client, outbox, db):
    h, _ = shopper(client, outbox)
    client.patch("/account/me", headers=h, json={"phone": "9" + "8" * 9})
    number = client.post(
        "/account/enquiry",
        headers=h,
        json={"what": "Temple door", "description": "Carved Ganesha, 7 feet"},
    ).json()["order_number"]
    r = start(client, h, number)
    assert r.status_code == 400 and "Nothing is due" in r.json()["detail"]


def test_asking_again_for_the_same_amount_reuses_the_open_payment(
    client, outbox, db, razorpay
):
    item = make_item(db)
    h, number = placed(client, outbox, db, item)
    first = start(client, h, number).json()
    second = start(client, h, number).json()
    assert first["razorpay_order_id"] == second["razorpay_order_id"]
    assert len(razorpay) == 1
    other = start(client, h, number, amount="500").json()
    assert (
        other["razorpay_order_id"] != first["razorpay_order_id"] and len(razorpay) == 2
    )


def test_made_to_order_pieces_are_not_reserved(client, outbox, db):
    item = make_item(db, name="Carved Door", stock=0, made_to_order=True)
    h, number = placed(client, outbox, db, item, qty=1)
    assert start(client, h, number).status_code == 200
    assert reservations(db, row(db, number)) == []


def test_nobody_can_start_paying_for_someone_elses_order(client, outbox, db):
    item = make_item(db)
    _, number = placed(client, outbox, db, item)
    thief, _ = shopper(client, outbox, name="Ravi Kumar")
    assert start(client, thief, number).status_code == 404
    assert client.post(f"/account/orders/{number}/pay", json={}).status_code == 401


# --------------------------------------------------------------------- reservations
def test_reserved_stock_cannot_be_promised_to_another_shopper(client, outbox, db):
    item = make_item(db, stock=1)
    first_h, first = placed(client, outbox, db, item, qty=1)
    second_h, second = placed(
        client, outbox, db, item, qty=1
    )  # one left: both can order

    assert start(client, first_h, first).status_code == 200
    blocked = start(client, second_h, second)
    assert blocked.status_code == 400 and "Not enough stock" in blocked.json()["detail"]
    assert reservations(db, row(db, second)) == []

    # a brand new order for the same unit is refused at checkout too
    h, _ = shopper(client, outbox, name="Meena")
    address = save_address(client, h)
    late = order_with(client, h, item, address_id=address["id"])
    assert late.status_code == 400 and "stock" in late.json()["detail"].lower()


def test_an_unpaid_hold_runs_out_and_the_unit_is_free_again(client, outbox, db):
    item = make_item(db, stock=1)
    first_h, first = placed(client, outbox, db, item, qty=1)
    second_h, second = placed(client, outbox, db, item, qty=1)
    start(client, first_h, first)
    held = reservations(db, row(db, first))[0]
    held.expires_at = held.expires_at - timedelta(hours=1)
    db.commit()
    assert start(client, second_h, second).status_code == 200


def test_the_stock_hold_is_not_taken_by_a_counter_sale(client, outbox, db, client_as):
    item = make_item(db, stock=3)
    h, number = placed(client, outbox, db, item, qty=2)
    start(client, h, number)
    owner, _ = client_as("owner")

    too_many = owner.post(
        "/sales/", json={"items": [{"item_id": str(item.id), "quantity": 2}]}
    )
    assert too_many.status_code == 400 and "Insufficient" in too_many.json()["detail"]
    assert "Available: 1" in too_many.json()["detail"]
    one = owner.post(
        "/sales/", json={"items": [{"item_id": str(item.id), "quantity": 1}]}
    )
    assert one.status_code == 200, one.text


# ------------------------------------------------------- confirming from the browser
def confirm(client, h, number, rzp_order, payment, sig=None):
    return client.post(
        f"/account/orders/{number}/pay/verify",
        headers=h,
        json={
            "razorpay_order_id": rzp_order,
            "razorpay_payment_id": payment,
            "razorpay_signature": sig or checkout_sig(rzp_order, payment),
        },
    )


def test_a_signed_payment_is_credited_once_and_confirms_the_order(client, outbox, db):
    item = make_item(db, stock=3, price="1000.00")
    h, number = placed(client, outbox, db, item, qty=2)
    rzp = start(client, h, number).json()["razorpay_order_id"]
    payment = pay_id()

    r = confirm(client, h, number, rzp, payment)
    assert r.status_code == 200, r.text
    assert (
        Decimal(r.json()["advance_paid"]) == 2000
        and Decimal(r.json()["balance_due"]) == 0
    )
    assert r.json()["status"] == "confirmed"

    order = row(db, number)
    [paid] = payments_of(db, order)
    assert (paid.amount, paid.reference, paid.created_by) == (
        Decimal("2000.00"),
        payment,
        None,
    )
    assert order.advance_paid == Decimal("2000.00") and order.status == "confirmed"
    intent = (
        db.query(PaymentIntent).filter(PaymentIntent.provider_order_id == rzp).one()
    )
    assert intent.status == "paid" and intent.provider_payment_id == payment
    [held] = reservations(db, order)
    assert held.expires_at is None and held.released_at is None  # held until billed

    again = confirm(client, h, number, rzp, payment)  # a double click
    assert again.status_code == 200 and len(payments_of(db, order)) == 1
    assert row(db, number).advance_paid == Decimal("2000.00")


def test_a_wrong_signature_credits_nothing(client, outbox, db):
    item = make_item(db)
    h, number = placed(client, outbox, db, item)
    rzp = start(client, h, number).json()["razorpay_order_id"]
    payment = pay_id()
    for sig in (
        "",
        "deadbeef",
        checkout_sig(rzp, pay_id()),
        checkout_sig(rzp, payment, "other"),
    ):
        r = confirm(client, h, number, rzp, payment, sig=sig or "x")
        assert r.status_code == 400, sig
    order = row(db, number)
    assert payments_of(db, order) == [] and order.advance_paid in (None, 0)
    assert order.status == "draft"


def test_a_payment_for_another_ordrs_razorpay_order_is_refused(client, outbox, db):
    item = make_item(db, stock=10)
    mine_h, mine = placed(client, outbox, db, item)
    theirs_h, theirs = placed(client, outbox, db, item)
    their_rzp = start(client, theirs_h, theirs).json()["razorpay_order_id"]
    payment = pay_id()
    r = confirm(client, mine_h, mine, their_rzp, payment)  # validly signed, wrong order
    assert r.status_code == 404
    assert (
        payments_of(db, row(db, mine)) == [] and payments_of(db, row(db, theirs)) == []
    )
    assert confirm(client, mine_h, mine, "order_nope", pay_id()).status_code == 404


def test_an_advance_then_the_balance(client, outbox, db):
    item = make_item(db, stock=3, price="1000.00")
    h, number = placed(client, outbox, db, item, qty=2)
    rzp1 = start(client, h, number, amount="500").json()["razorpay_order_id"]
    assert confirm(client, h, number, rzp1, pay_id()).status_code == 200
    view = client.get(f"/account/orders/{number}", headers=h).json()
    assert Decimal(view["advance_paid"]) == 500 and Decimal(view["balance_due"]) == 1500

    second = start(client, h, number).json()
    assert second["amount_paise"] == 150000  # the rest
    assert (
        confirm(client, h, number, second["razorpay_order_id"], pay_id()).status_code
        == 200
    )
    assert (
        Decimal(
            client.get(f"/account/orders/{number}", headers=h).json()["balance_due"]
        )
        == 0
    )
    assert start(client, h, number).status_code == 400  # nothing left to pay


# -------------------------------------------------------------------------- webhook
def test_the_webhook_needs_a_good_signature_and_valid_json(client):
    bad = webhook(client, "payment.captured", "order_x", "pay_x", 100, secret="wrong")
    assert bad.status_code == 400
    raw = b"not json at all"
    sig = hmac.new(WEBHOOK_SECRET.encode(), raw, hashlib.sha256).hexdigest()
    junk = client.post(
        "/payments/razorpay/webhook", content=raw, headers={"X-Razorpay-Signature": sig}
    )
    assert junk.status_code == 400
    assert client.post("/payments/razorpay/webhook", content=b"{}").status_code == 400


def test_a_captured_payment_over_the_webhook_is_credited_once_even_if_repeated(
    client, outbox, db
):
    item = make_item(db, stock=3, price="1000.00")
    h, number = placed(client, outbox, db, item, qty=2)
    rzp = start(client, h, number).json()["razorpay_order_id"]
    payment = pay_id()

    first = webhook(client, "payment.captured", rzp, payment, 200000)
    assert first.status_code == 200 and first.json() == {"status": "credited"}
    again = webhook(client, "payment.captured", rzp, payment, 200000)
    assert again.status_code == 200 and again.json() == {"status": "duplicate"}
    assert confirm(client, h, number, rzp, payment).status_code == 200  # browser too

    order = row(db, number)
    assert len(payments_of(db, order)) == 1
    assert order.advance_paid == Decimal("2000.00") and order.status == "confirmed"
    assert reservations(db, order)[0].expires_at is None


def test_a_payment_of_the_wrong_amount_is_flagged_not_credited(client, outbox, db):
    item = make_item(db, price="1000.00")
    h, number = placed(client, outbox, db, item, qty=2)
    rzp = start(client, h, number).json()["razorpay_order_id"]
    r = webhook(client, "payment.captured", rzp, pay_id(), 100)  # Rs 1, not Rs 2000
    assert r.status_code == 200 and r.json() == {"status": "mismatch"}
    order = row(db, number)
    assert payments_of(db, order) == [] and order.status == "draft"
    intent = (
        db.query(PaymentIntent).filter(PaymentIntent.provider_order_id == rzp).one()
    )
    assert intent.status == "mismatch"


def test_unknown_and_unrelated_events_are_acknowledged_and_ignored(client, db):
    unknown = webhook(client, "payment.captured", "order_not_ours", "pay_1", 500)
    assert unknown.status_code == 200 and unknown.json() == {"status": "unknown"}
    other = webhook(client, "refund.processed", "order_not_ours", "pay_1", 500)
    assert other.status_code == 200 and other.json() == {"status": "ignored"}
    no_order = webhook(client, "payment.captured", None, "pay_1", 500)
    assert no_order.json() == {"status": "ignored"}


def test_a_failed_attempt_is_noted_and_the_shopper_can_still_pay(client, outbox, db):
    item = make_item(db, price="1000.00")
    h, number = placed(client, outbox, db, item, qty=1)
    rzp = start(client, h, number).json()["razorpay_order_id"]
    failed = webhook(client, "payment.failed", rzp, pay_id(), 100000)
    assert failed.json() == {"status": "failed"}
    intent = (
        db.query(PaymentIntent).filter(PaymentIntent.provider_order_id == rzp).one()
    )
    db.refresh(intent)
    assert intent.status == "failed"
    order = row(db, number)
    assert order.status == "draft" and payments_of(db, order) == []
    assert reservations(db, order) != []  # the hold stays until it runs out

    retry = webhook(client, "payment.captured", rzp, pay_id(), 100000)
    assert retry.json() == {"status": "credited"}  # same Razorpay order, second try
    assert row(db, number).status == "confirmed"


# ------------------------------------------------------------- cancel and billing
def test_cancelling_lets_the_stock_go_and_stops_new_payments(
    client, outbox, db, client_as
):
    item = make_item(db, stock=1)
    h, number = placed(client, outbox, db, item, qty=1)
    start(client, h, number)
    order = row(db, number)
    owner, _ = client_as("owner")
    done = owner.patch(f"/orders/{order.id}/status", json={"status": "cancelled"})
    assert done.status_code == 200, done.text
    assert all(r.released_at for r in reservations(db, order))
    assert start(client, h, number).status_code == 409


def test_money_that_arrives_for_a_cancelled_order_is_recorded_for_a_refund(
    client, outbox, db, client_as
):
    item = make_item(db, price="1000.00")
    h, number = placed(client, outbox, db, item, qty=1)
    rzp = start(client, h, number).json()["razorpay_order_id"]
    order = row(db, number)
    owner, _ = client_as("owner")
    owner.patch(f"/orders/{order.id}/status", json={"status": "cancelled"})

    r = webhook(client, "payment.captured", rzp, pay_id(), 100000)
    assert r.json() == {"status": "credited"}  # the money is real: it is written down
    order = row(db, number)
    assert order.status == "cancelled" and order.advance_paid == Decimal("1000.00")
    assert all(x.released_at for x in reservations(db, order))


def test_billing_a_paid_order_uses_its_own_hold_and_lets_it_go(
    client, outbox, db, client_as
):
    item = make_item(db, stock=3, price="1000.00")
    h, number = placed(client, outbox, db, item, qty=2)
    rzp = start(client, h, number).json()["razorpay_order_id"]
    assert confirm(client, h, number, rzp, pay_id()).status_code == 200
    order = row(db, number)

    owner, _ = client_as("owner")
    sold = owner.post(
        "/sales/", json={"items": [{"item_id": str(item.id), "quantity": 1}]}
    )
    assert sold.status_code == 200  # 3 on the shelf, 2 held: one is free to sell
    billed = owner.post(f"/orders/{order.id}/invoice")
    assert billed.status_code == 201, billed.text  # its own two are not blocked
    db.refresh(item)
    assert item.current_stock == 0
    assert all(r.released_at for r in reservations(db, order))
    assert row(db, number).sale_id is not None


# ---------------------------------------------------------------------------- guests
def test_a_guest_can_pay_with_the_order_number_and_phone(client, db):
    item = make_item(db, stock=3, price="1000.00")
    phone = "9" + "".join(str(uuid.uuid4().int % 10) for _ in range(9))
    order = client.post(
        "/public/checkout",
        json={
            "name": "Meena",
            "phone": phone,
            "address": "12 Temple Road",
            "town": "Puttur",
            "pincode": "574201",
            "items": [{"slug": item.slug, "quantity": 1}],
        },
    ).json()
    number = order["order_number"]

    wrong = client.post(f"/public/orders/{number}/pay", json={"phone": "9000000000"})
    assert wrong.status_code == 404
    r = client.post(f"/public/orders/{number}/pay", json={"phone": phone})
    assert r.status_code == 200, r.text
    rzp, payment = r.json()["razorpay_order_id"], pay_id()
    assert r.json()["amount_paise"] == 100000

    bad = client.post(
        f"/public/orders/{number}/pay/verify",
        json={
            "phone": phone,
            "razorpay_order_id": rzp,
            "razorpay_payment_id": payment,
            "razorpay_signature": "nope",
        },
    )
    assert bad.status_code == 400
    ok = client.post(
        f"/public/orders/{number}/pay/verify",
        json={
            "phone": phone,
            "razorpay_order_id": rzp,
            "razorpay_payment_id": payment,
            "razorpay_signature": checkout_sig(rzp, payment),
        },
    )
    assert ok.status_code == 200 and ok.json()["status"] == "confirmed"
    assert Decimal(ok.json()["balance_due"]) == 0
