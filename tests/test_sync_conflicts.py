import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from datetime import datetime, timedelta
from uuid import uuid4
from app.models.item import Item
from app.models.sale import Sale


def test_conflict_lww_newer_wins(auth_client: TestClient, db: Session):
    """Test that a newer timestamp update overwrites an existing record (LWW)."""
    sale_id = str(uuid4())

    # 0. Create a customer to satisfy FK constraint
    cust_resp = auth_client.post(
        "/customers/",
        json={"name": "Test Customer", "phone": str(uuid4().int % 10**10)},
    )
    customer_id = cust_resp.json()["id"]

    # 1. Create a sale via sync first (ensures correct user ownership)
    initial_time = datetime.now() - timedelta(minutes=10)
    create_payload = {
        "operations": [
            {
                "id": str(uuid4()),
                "entity": "sale",
                "action": "create",
                "payload": {
                    "id": sale_id,
                    "customer_id": customer_id,
                    "items": [],
                    "total_amount": 100.0,
                    "payment_method": "cash",
                },
                "updated_at": initial_time.isoformat(),
            }
        ]
    }
    auth_client.post("/sync/push", json=create_payload)

    # 2. Push a newer update
    newer_time = datetime.now()
    update_payload = {
        "operations": [
            {
                "id": str(uuid4()),
                "entity": "sale",
                "action": "update",
                "payload": {
                    "id": sale_id,
                    "customer_id": customer_id,
                    "items": [],
                    "total_amount": 200.0,
                    "payment_method": "cash",
                },
                "updated_at": newer_time.isoformat(),
            }
        ]
    }
    resp = auth_client.post("/sync/push", json=update_payload)
    assert resp.status_code == 200

    sale = db.query(Sale).filter(Sale.id == sale_id).first()
    assert float(sale.total_amount) == 200.0


def test_conflict_lww_older_ignored(auth_client: TestClient, db: Session):
    """Test that an older timestamp update is ignored (LWW)."""
    sale_id = str(uuid4())
    current_time = datetime.now()

    # 0. Create a customer to satisfy FK constraint
    cust_resp = auth_client.post(
        "/customers/",
        json={"name": "Test Customer 2", "phone": str(uuid4().int % 10**10)},
    )
    customer_id = cust_resp.json()["id"]

    # 1. Create a sale via sync
    create_payload = {
        "operations": [
            {
                "id": str(uuid4()),
                "entity": "sale",
                "action": "create",
                "payload": {
                    "id": sale_id,
                    "customer_id": customer_id,
                    "items": [],
                    "total_amount": 500.0,
                    "payment_method": "cash",
                },
                "updated_at": current_time.isoformat(),
            }
        ]
    }
    auth_client.post("/sync/push", json=create_payload)

    # 2. Push an older update
    older_time = current_time - timedelta(minutes=5)
    update_payload = {
        "operations": [
            {
                "id": str(uuid4()),
                "entity": "sale",
                "action": "update",
                "payload": {
                    "id": sale_id,
                    "customer_id": customer_id,
                    "items": [],
                    "total_amount": 100.0,
                    "payment_method": "cash",
                },
                "updated_at": older_time.isoformat(),
            }
        ]
    }
    resp = auth_client.post("/sync/push", json=update_payload)
    assert resp.status_code == 200

    sale = db.query(Sale).filter(Sale.id == sale_id).first()
    assert float(sale.total_amount) == 500.0


def test_conflict_stock_delta(auth_client: TestClient, db: Session):
    """Test that item stock updates are additive (StockDelta)."""
    item_id = str(uuid4())
    sku = f"SKU-{uuid4().hex[:6]}"

    # 1. Create an item via sync
    create_payload = {
        "operations": [
            {
                "id": str(uuid4()),
                "entity": "item",
                "action": "create",
                "payload": {
                    "id": item_id,
                    "name": "Test Item",
                    "sku": sku,
                    "selling_price": 10.0,
                    "current_stock": 10,
                },
                "updated_at": datetime.now().isoformat(),
            }
        ]
    }
    auth_client.post("/sync/push", json=create_payload)

    # 2. Push two updates with stock deltas
    update_payload = {
        "operations": [
            {
                "id": str(uuid4()),
                "entity": "item",
                "action": "update",
                "payload": {
                    "id": item_id,
                    "name": "Test Item",
                    "sku": sku,
                    "selling_price": 10.0,
                    "current_stock": 5,
                },
                "updated_at": datetime.now().isoformat(),
            },
            {
                "id": str(uuid4()),
                "entity": "item",
                "action": "update",
                "payload": {
                    "id": item_id,
                    "name": "Test Item",
                    "sku": sku,
                    "selling_price": 10.0,
                    "current_stock": 3,
                },
                "updated_at": datetime.now().isoformat(),
            },
        ]
    }
    resp = auth_client.post("/sync/push", json=update_payload)
    assert resp.status_code == 200

    item = db.query(Item).filter(Item.id == item_id).first()
    assert float(item.current_stock) == 18.0
