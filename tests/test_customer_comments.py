"""
Customer comments: only someone who knows the order number AND its phone can write one,
only once the order is confirmed, one per order; nothing shows until staff approve it.
"""

import pytest
from fastapi.testclient import TestClient

from app.core import ratelimit
from tests.test_public_api import PHONE, checkout_body, make_item


@pytest.fixture(autouse=True)
def _fresh_limits(monkeypatch):
    from app.core.config import settings

    ratelimit.reset()
    monkeypatch.setattr(settings, "PUBLIC_RATE_LIMIT", 100)
    yield
    ratelimit.reset()


def place_order(c):
    """A website order for one product, as a customer would place it."""
    item = make_item(c)
    r = c.post("/public/checkout", json=checkout_body(item["slug"]))
    assert r.status_code == 201, r.text
    return r.json()


def shown_for(c, order):
    """Approved comments on the website that belong to this order (the database is shared between tests)."""
    return [
        x
        for x in c.get("/public/comments").json()
        if x["name"] == f"Buyer {order['order_number']}"
    ]


def staff_for(c, order, status="pending"):
    return [
        x
        for x in c.get("/comments", params={"status": status}).json()
        if x["order_number"] == order["order_number"]
    ]


def body(order, **over):
    d = {
        "order_number": order["order_number"],
        "phone": PHONE,
        "display_name": f"Buyer {order['order_number']}",
        "place": "Chennai",
        "text": "The headboard carving is even better in person. It arrived on the day promised.",
        "consent": True,
    }
    d.update(over)
    return d


def test_a_draft_order_cannot_comment_yet(auth_client: TestClient):
    order = place_order(auth_client)
    r = auth_client.post("/public/comments", json=body(order))
    assert r.status_code == 409 and "confirmed" in r.json()["detail"]
    assert shown_for(auth_client, order) == []


def test_wrong_phone_or_unknown_order_looks_the_same(auth_client):
    order = place_order(auth_client)
    confirm(auth_client, order)
    wrong = auth_client.post("/public/comments", json=body(order, phone="9123456780"))
    unknown = auth_client.post(
        "/public/comments", json=body(order, order_number="ORD-1999-0001")
    )
    assert wrong.status_code == unknown.status_code == 404
    assert wrong.json() == unknown.json()


def confirm(auth_client, order):
    orders = auth_client.get("/orders/").json()
    oid = next(o["id"] for o in orders if o["order_number"] == order["order_number"])
    r = auth_client.patch(f"/orders/{oid}/status", json={"status": "confirmed"})
    assert r.status_code == 200, r.text


def test_comment_is_held_until_staff_approve_it(auth_client):
    order = place_order(auth_client)
    confirm(auth_client, order)

    r = auth_client.post("/public/comments", json=body(order))
    assert r.status_code == 201 and r.json() == {"status": "received"}
    assert shown_for(auth_client, order) == []  # nothing shows yet

    pending = staff_for(auth_client, order)
    assert len(pending) == 1
    assert pending[0]["status"] == "pending"

    ok = auth_client.patch(f"/comments/{pending[0]['id']}", json={"status": "approved"})
    assert ok.status_code == 200 and ok.json()["status"] == "approved"
    shown = shown_for(auth_client, order)
    assert len(shown) == 1 and shown[0]["place"] == "Chennai"
    assert set(shown[0]) == {"name", "place", "piece", "text"}  # nothing else leaks

    hide = auth_client.patch(f"/comments/{pending[0]['id']}", json={"status": "hidden"})
    assert hide.status_code == 200
    assert shown_for(auth_client, order) == []


def test_one_comment_per_order(auth_client):
    order = place_order(auth_client)
    confirm(auth_client, order)
    assert auth_client.post("/public/comments", json=body(order)).status_code == 201
    again = auth_client.post(
        "/public/comments", json=body(order, text="A second, different comment here.")
    )
    assert again.status_code == 409


def test_consent_is_required_and_short_comments_are_refused(auth_client):
    order = place_order(auth_client)
    confirm(auth_client, order)
    assert (
        auth_client.post(
            "/public/comments", json=body(order, consent=False)
        ).status_code
        == 422
    )
    assert (
        auth_client.post("/public/comments", json=body(order, text="Nice")).status_code
        == 422
    )
    assert staff_for(auth_client, order, "all") == []


def test_the_hidden_spam_field_stores_nothing(auth_client):
    order = place_order(auth_client)
    confirm(auth_client, order)
    r = auth_client.post(
        "/public/comments", json=body(order, website="http://spam.example")
    )
    assert r.status_code == 201  # a bot is told it worked
    assert staff_for(auth_client, order, "all") == []


def test_workers_cannot_moderate(worker_client):
    assert worker_client.get("/comments").status_code == 403
    r = worker_client.patch(
        "/comments/00000000-0000-0000-0000-000000000001", json={"status": "approved"}
    )
    assert r.status_code == 403


def test_moderation_needs_a_login(client):
    assert client.get("/comments").status_code == 401


def test_the_review_page_is_served_with_a_tight_policy(client):
    r = client.get("/comments/review")
    assert r.status_code == 200 and "Customer comments" in r.text
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert r.headers["cache-control"] == "no-store"
