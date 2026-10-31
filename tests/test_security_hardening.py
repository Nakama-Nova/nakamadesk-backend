"""Hardening: markup in bill PDFs, wages over sync, response headers, caller address."""

import uuid
from datetime import date, datetime, timezone

from starlette.requests import Request

from app.core import ratelimit
from app.models.attendance import Attendance


# ------------------------------------------------------------------ caller address
def _request(headers=None, client=("10.1.1.1", 5000)):
    raw = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    return Request({"type": "http", "headers": raw, "client": client})


def test_forwarded_header_is_ignored_off_vercel(monkeypatch):
    monkeypatch.delenv("VERCEL", raising=False)
    monkeypatch.delenv("TRUST_PROXY_HEADERS", raising=False)
    spoofed = _request({"X-Forwarded-For": "1.2.3.4"})
    assert ratelimit.client_ip(spoofed) == "10.1.1.1"


def test_forwarded_header_is_used_on_vercel(monkeypatch):
    monkeypatch.setenv("VERCEL", "1")
    both = _request({"x-vercel-forwarded-for": "1.2.3.4", "x-forwarded-for": "9.9.9.9"})
    assert ratelimit.client_ip(both) == "1.2.3.4"
    assert ratelimit.client_ip(_request({"X-Forwarded-For": "5.6.7.8, 10.0.0.1"})) == (
        "5.6.7.8"
    )
    assert ratelimit.client_ip(_request()) == "10.1.1.1"


# ------------------------------------------------------------------ response headers
def test_security_headers_are_on_responses(client):
    r = client.get("/health/")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["referrer-policy"] == "no-referrer"
    assert "max-age=" in r.headers["strict-transport-security"]


def test_lab_page_keeps_its_own_csp(client):
    r = client.get(f"/lab/view/{uuid.uuid4()}")
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert r.headers["x-frame-options"] == "DENY"


# ------------------------------------------------------------------ bill PDF
def test_customer_name_with_markup_still_prints_and_is_not_fetched(auth_client):
    item = auth_client.post(
        "/items/",
        json={
            "sku": f"PDF-{uuid.uuid4().hex[:6]}",
            "name": "Stool",
            "selling_price": 1000,
            "current_stock": 5,
        },
    ).json()
    uid = uuid.uuid4().hex[:8]
    customer = auth_client.post(
        "/customers/",
        json={
            "name": 'A <b>Traders & <img src="http://127.0.0.1:9/x.png"/>',
            "email": f"c{uid}@example.com",
            "phone": "9" + str(int(uid, 16))[:9].ljust(9, "0"),
            "address": "Bangalore",
        },
    )
    assert customer.status_code == 200, customer.text
    sale = auth_client.post(
        "/sales/",
        json={
            "customer_id": customer.json()["id"],
            "items": [{"item_id": item["id"], "quantity": 1}],
        },
    ).json()
    pdf = auth_client.get(f"/invoices/{sale['invoice_number']}/pdf")
    assert pdf.status_code == 200
    assert pdf.headers["content-type"] == "application/pdf"


# ------------------------------------------------------------------ wages over sync
def _push_attendance(client, att_id, action, **payload):
    op = {
        "id": str(uuid.uuid4()),
        "entity": "attendance",
        "action": action,
        "payload": {"id": att_id, **payload},
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    r = client.post("/sync/push", json={"operations": [op]})
    assert r.status_code == 200, r.text
    assert not r.json()["failed"], r.json()


def test_sales_user_cannot_set_own_wage_through_sync(client_as, db):
    client, user = client_as("sales")
    att_id = str(uuid.uuid4())
    _push_attendance(
        client,
        att_id,
        "create",
        user_id=str(user.id),
        date=date.today().isoformat(),
        status="present",
        daily_wage=99999,
        payment_status="paid",
    )
    row = db.get(Attendance, uuid.UUID(att_id))
    assert float(row.daily_wage) == float(user.base_daily_wage or 0)
    assert row.payment_status == "pending"

    # nor raise it afterwards
    _push_attendance(
        client,
        att_id,
        "update",
        user_id=str(user.id),
        date=date.today().isoformat(),
        status="present",
        daily_wage=99999,
        payment_status="paid",
    )
    db.refresh(row)
    assert float(row.daily_wage) == float(user.base_daily_wage or 0)
    assert row.payment_status == "pending"
