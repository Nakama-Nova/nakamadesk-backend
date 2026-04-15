"""
Tests for Order Management — Day 1 feature expansion.

Tests the full order lifecycle: creation (standard + custom), listing,
retrieval, status transitions, and edge cases (invalid items, bad
transitions). Uses the same auth_client / db fixture pattern from conftest.py.
"""

import uuid

from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _create_customer(auth_client: TestClient) -> str:
    """Create a test customer and return its ID."""
    response = auth_client.post(
        "/customers/",
        json={
            "name": f"Test Customer {uuid.uuid4().hex[:6]}",
            "phone": f"9{uuid.uuid4().int % 10**9:09d}",
            "customer_type": "retail",
        },
    )
    assert response.status_code in (200, 201), response.json()
    return response.json()["id"]


def _create_item(auth_client: TestClient, name: str = "Teak Chair") -> str:
    """Create a catalogue item and return its ID."""
    response = auth_client.post(
        "/items/",
        json={
            "sku": f"SKU-{uuid.uuid4().hex[:8]}",
            "name": name,
            "selling_price": 12000.0,
            "current_stock": 5,
            "gst_percent": 12.0,
            "hsn_code": "9403",
        },
    )
    assert response.status_code in (200, 201), response.json()
    return response.json()["id"]


# ---------------------------------------------------------------------------
# Order creation — positive paths
# ---------------------------------------------------------------------------


def test_create_standard_order(auth_client: TestClient):
    """A standard order with a catalogue item should persist correctly."""
    customer_id = _create_customer(auth_client)
    item_id = _create_item(auth_client)

    payload = {
        "customer_id": customer_id,
        "order_type": "standard",
        "estimated_amount": 12000.00,
        "advance_paid": 5000.00,
        "expected_delivery": "2026-06-30",
        "items": [{"item_id": item_id, "quantity": 1, "unit_price": 12000.00}],
        "notes": "Standard teak chair order",
    }
    response = auth_client.post("/orders/", json=payload)
    assert response.status_code == 201, response.json()

    data = response.json()
    assert data["order_type"] == "standard"
    assert data["status"] == "draft"
    assert data["customer_id"] == customer_id
    assert data["order_number"].startswith("ORD-")
    assert len(data["items"]) == 1
    assert data["items"][0]["item_id"] == item_id
    assert float(data["balance_due"]) == 7000.00


def test_create_custom_order(auth_client: TestClient):
    """A custom order with no catalogue item should persist with custom_specs."""
    payload = {
        "order_type": "custom",
        "estimated_amount": 85000.00,
        "advance_paid": 25000.00,
        "custom_specs": {
            "wood_type": "teak",
            "dimensions": "6x3 ft",
            "motif": "temple-arch",
        },
        "expected_delivery": "2026-08-15",
        "items": [
            {
                "item_name": "Custom Temple Door Panel",
                "quantity": 1,
                "unit_price": 85000.00,
                "notes": "With arch motif on top",
            }
        ],
    }
    response = auth_client.post("/orders/", json=payload)
    assert response.status_code == 201, response.json()

    data = response.json()
    assert data["order_type"] == "custom"
    assert data["status"] == "draft"
    assert data["custom_specs"]["wood_type"] == "teak"
    assert data["items"][0]["item_id"] is None
    assert data["items"][0]["item_name"] == "Custom Temple Door Panel"
    assert float(data["balance_due"]) == 60000.00


def test_order_number_is_sequential(auth_client: TestClient):
    """Each new order should get an incrementing number."""
    item_id = _create_item(auth_client, "Neem Shelf")

    def make():
        return auth_client.post(
            "/orders/",
            json={
                "order_type": "standard",
                "items": [{"item_id": item_id, "quantity": 1, "unit_price": 3000}],
            },
        )

    r1 = make()
    r2 = make()
    assert r1.status_code == 201
    assert r2.status_code == 201

    num1 = int(r1.json()["order_number"].split("-")[-1])
    num2 = int(r2.json()["order_number"].split("-")[-1])
    assert num2 == num1 + 1


# ---------------------------------------------------------------------------
# Order creation — validation / edge cases
# ---------------------------------------------------------------------------


def test_create_order_rejects_nonexistent_item(auth_client: TestClient):
    """Should return 404 when line item references an unknown item_id."""
    response = auth_client.post(
        "/orders/",
        json={
            "order_type": "standard",
            "items": [{"item_id": str(uuid.uuid4()), "quantity": 1, "unit_price": 100}],
        },
    )
    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


def test_create_order_rejects_item_without_identity(auth_client: TestClient):
    """Should return 422 when an item has neither item_id nor item_name."""
    response = auth_client.post(
        "/orders/",
        json={
            "order_type": "standard",
            "items": [{"quantity": 1, "unit_price": 100}],
        },
    )
    assert response.status_code == 422


def test_create_order_rejects_empty_items(auth_client: TestClient):
    """Should return 422 when no line items are provided."""
    response = auth_client.post(
        "/orders/",
        json={"order_type": "standard", "items": []},
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Retrieval
# ---------------------------------------------------------------------------


def test_list_and_get_order(auth_client: TestClient):
    """Created orders should appear in list and be fetchable by ID."""
    item_id = _create_item(auth_client)
    r = auth_client.post(
        "/orders/",
        json={
            "order_type": "standard",
            "items": [{"item_id": item_id, "quantity": 2, "unit_price": 5000}],
        },
    )
    assert r.status_code == 201
    order_id = r.json()["id"]

    # List
    list_r = auth_client.get("/orders/")
    assert list_r.status_code == 200
    ids = [o["id"] for o in list_r.json()]
    assert order_id in ids

    # Get by ID
    get_r = auth_client.get(f"/orders/{order_id}")
    assert get_r.status_code == 200
    assert get_r.json()["id"] == order_id
    assert len(get_r.json()["items"]) == 1


def test_get_nonexistent_order_returns_404(auth_client: TestClient):
    response = auth_client.get(f"/orders/{uuid.uuid4()}")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Status transitions
# ---------------------------------------------------------------------------


def test_valid_status_transitions(auth_client: TestClient):
    """draft → confirmed → in_production should all succeed."""
    item_id = _create_item(auth_client)
    r = auth_client.post(
        "/orders/",
        json={
            "order_type": "standard",
            "items": [{"item_id": item_id, "quantity": 1, "unit_price": 1000}],
        },
    )
    order_id = r.json()["id"]

    # draft → confirmed
    r2 = auth_client.patch(f"/orders/{order_id}/status", json={"status": "confirmed"})
    assert r2.status_code == 200
    assert r2.json()["status"] == "confirmed"

    # confirmed → in_production
    r3 = auth_client.patch(
        f"/orders/{order_id}/status", json={"status": "in_production"}
    )
    assert r3.status_code == 200
    assert r3.json()["status"] == "in_production"


def test_invalid_status_transition_rejected(auth_client: TestClient):
    """draft → delivered (skipping steps) must be rejected."""
    item_id = _create_item(auth_client)
    r = auth_client.post(
        "/orders/",
        json={
            "order_type": "standard",
            "items": [{"item_id": item_id, "quantity": 1, "unit_price": 1000}],
        },
    )
    order_id = r.json()["id"]

    r2 = auth_client.patch(f"/orders/{order_id}/status", json={"status": "delivered"})
    assert r2.status_code == 400
    assert "Cannot transition" in r2.json()["detail"]


def test_cancel_from_any_state(auth_client: TestClient):
    """Any active order can be cancelled."""
    item_id = _create_item(auth_client)
    r = auth_client.post(
        "/orders/",
        json={
            "order_type": "standard",
            "items": [{"item_id": item_id, "quantity": 1, "unit_price": 1000}],
        },
    )
    order_id = r.json()["id"]

    auth_client.patch(f"/orders/{order_id}/status", json={"status": "confirmed"})
    r2 = auth_client.patch(f"/orders/{order_id}/status", json={"status": "cancelled"})
    assert r2.status_code == 200
    assert r2.json()["status"] == "cancelled"


def test_delivered_order_sets_delivered_at(auth_client: TestClient):
    """Transitioning to 'delivered' should populate delivered_at timestamp."""
    item_id = _create_item(auth_client)
    r = auth_client.post(
        "/orders/",
        json={
            "order_type": "standard",
            "items": [{"item_id": item_id, "quantity": 1, "unit_price": 1000}],
        },
    )
    order_id = r.json()["id"]

    for status in ("confirmed", "in_production", "ready"):
        auth_client.patch(f"/orders/{order_id}/status", json={"status": status})

    r_delivered = auth_client.patch(
        f"/orders/{order_id}/status", json={"status": "delivered"}
    )
    assert r_delivered.status_code == 200
    assert r_delivered.json()["delivered_at"] is not None


# ---------------------------------------------------------------------------
# RBAC
# ---------------------------------------------------------------------------


def test_worker_cannot_create_order(worker_client: TestClient):
    """Workers must not be able to create orders (403)."""
    response = worker_client.post(
        "/orders/",
        json={
            "order_type": "standard",
            "items": [{"item_name": "Chair", "quantity": 1, "unit_price": 1000}],
        },
    )
    assert response.status_code == 403
