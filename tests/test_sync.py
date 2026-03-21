from fastapi.testclient import TestClient
from datetime import datetime, timezone, timedelta
import uuid


def test_push_sync_create_and_idempotency(auth_client: TestClient):
    # 1. Create offline
    client_id = str(uuid.uuid4())
    item_payload = {
        "sku": f"OFFLINE-{client_id[:8]}",
        "name": "Offline Item",
        "selling_price": 100.0,
        "current_stock": 10,
    }

    sync_req = {
        "operations": [
            {
                "id": client_id,
                "entity": "item",
                "action": "create",
                "payload": item_payload,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        ]
    }

    # 2. Sync Push
    resp = auth_client.post("/sync/push", json=sync_req)
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["success"]) == 1
    assert data["success"][0]["client_id"] == client_id

    # Verify DB
    items_resp = auth_client.get("/items/")
    assert any(i["sku"] == item_payload["sku"] for i in items_resp.json())

    # 3. Duplicate Idempotency
    resp2 = auth_client.post("/sync/push", json=sync_req)
    assert resp2.status_code == 200
    assert len(resp2.json()["success"]) == 1

    items_resp2 = auth_client.get("/items/")
    # Shouldn't duplicate
    count = sum(1 for i in items_resp2.json() if i["sku"] == item_payload["sku"])
    assert count == 1


def test_pull_sync(auth_client: TestClient):
    # 1. Pull with old timestamp
    old_time = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    resp = auth_client.get("/sync/pull", params={"last_sync": old_time})
    assert resp.status_code == 200
    data = resp.json()

    assert "items" in data
    assert "sales" in data
    assert "attendance" in data
    assert "raw_materials" in data


def test_sync_conflict_resolution(auth_client: TestClient):
    # Setup item
    item_id = auth_client.post(
        "/items/",
        json={
            "sku": f"CONF-{uuid.uuid4().hex[:4]}",
            "name": "Conflict Base",
            "selling_price": 50.0,
        },
    ).json()["id"]

    # Client A updates with older timestamp (should be rejected silently / LWW)
    client_a_time = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    op_a = {
        "id": str(uuid.uuid4()),
        "entity": "item",
        "action": "update",
        "payload": {
            "id": item_id,
            "name": "Older Update",
            "sku": f"CONF-{uuid.uuid4().hex[:4]}",
            "selling_price": 50.0,
            "current_stock": 0,
        },
        "updated_at": client_a_time,
    }
    auth_client.post("/sync/push", json={"operations": [op_a]})

    # Verify name hasn't changed because Server time is newer
    assert auth_client.get(f"/items/{item_id}").json()["name"] == "Conflict Base"

    # Client B updates with newer timestamp (Fast forward simulation)
    client_b_time = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    op_b = {
        "id": str(uuid.uuid4()),
        "entity": "item",
        "action": "update",
        "payload": {
            "id": item_id,
            "name": "Newer Update",
            "sku": f"CONF-{uuid.uuid4().hex[:4]}",
            "selling_price": 50.0,
            "current_stock": 0,
        },
        "updated_at": client_b_time,
    }
    resp_b = auth_client.post("/sync/push", json={"operations": [op_b]})
    assert resp_b.status_code == 200
    b_data = resp_b.json()
    assert len(b_data["failed"]) == 0, f"Sync B failed: {b_data['failed']}"

    # Verify name updated
    assert auth_client.get(f"/items/{item_id}").json()["name"] == "Newer Update"


def test_sync_partial_failure(auth_client: TestClient):
    op_good = {
        "id": str(uuid.uuid4()),
        "entity": "item",
        "action": "create",
        "payload": {
            "sku": str(uuid.uuid4()),
            "name": "Good",
            "selling_price": 10.0,
            "current_stock": 5,
        },
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    op_bad = {
        "id": str(uuid.uuid4()),
        "entity": "unknown_entity",
        "action": "create",
        "payload": {},
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }

    resp = auth_client.post("/sync/push", json={"operations": [op_good, op_bad]})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["success"]) == 1
    assert len(data["failed"]) == 1
    assert data["failed"][0]["client_id"] == op_bad["id"]
