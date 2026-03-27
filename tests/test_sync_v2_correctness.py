from datetime import datetime
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.sale import Sale
from app.models.user import User


def test_sync_invalid_action(auth_client: TestClient):
    """Test that invalid action strings are rejected by schema (Pydantic)."""
    payload = {
        "operations": [
            {
                "id": str(uuid4()),
                "entity": "sale",
                "action": "invalid_action",
                "payload": {
                    "id": str(uuid4()),
                    "items": [],
                    "total_amount": 0.0,
                    "payment_method": "cash",
                },
                "updated_at": datetime.now().isoformat(),
            }
        ]
    }
    resp = auth_client.post("/sync/push", json=payload)
    # Pydantic validation error (422) is expected
    assert resp.status_code == 422


def test_sync_unknown_entity(auth_client: TestClient):
    """Test that unknown entities are handled by the validator (service level)."""
    # We use an entity not in the registry but valid string for Pydantic
    payload = {
        "operations": [
            {
                "id": str(uuid4()),
                "entity": "non_existent_entity",
                "action": "create",
                "payload": {"id": str(uuid4())},
                "updated_at": datetime.now().isoformat(),
            }
        ]
    }
    resp = auth_client.post("/sync/push", json=payload)
    assert resp.status_code == 200
    failed = resp.json()["failed"]
    assert len(failed) == 1
    assert "unknown entity" in failed[0]["error"].lower()


def test_sync_cross_user_update_restriction(db: Session, worker_client: TestClient):
    """Test that User B cannot update User A's created record."""
    # 1. Create User A manually
    user_a = User(
        username=f"usera_{uuid4().hex[:8]}",
        email=f"a_{uuid4().hex[:8]}@example.com",
        password_hash="hash",
    )
    db.add(user_a)
    db.commit()
    db.refresh(user_a)

    # 2. Create a record for User A
    record_id = uuid4()
    sale = Sale(
        id=record_id,
        user_id=user_a.id,
        total_amount=100.0,
        invoice_number=f"INV-A-{uuid4().hex[:8]}",
    )
    db.add(sale)
    db.commit()

    # 3. User B (worker_client) tries to update User A's record via sync
    client_id_b = str(uuid4())
    payload_update = {
        "operations": [
            {
                "id": client_id_b,
                "entity": "sale",
                "action": "update",
                "payload": {
                    "id": str(record_id),
                    "customer_id": str(
                        uuid4()
                    ),  # Partial update logic handles this if it doesn't exist? No, it needs to pass Pydantic.
                    "items": [],
                    "total_amount": 200.0,
                    "payment_method": "card",
                },
                "updated_at": datetime.now().isoformat(),
            }
        ]
    }
    resp_update = worker_client.post("/sync/push", json=payload_update)
    assert resp_update.status_code == 200
    # The operation should be in the 'failed' list due to access denied
    failed = resp_update.json()["failed"]
    assert len(failed) == 1
    assert failed[0]["client_id"] == client_id_b
    assert "access denied" in failed[0]["error"].lower()


def test_sync_cross_user_delete_restriction(db: Session, worker_client: TestClient):
    """Test that User B cannot delete User A's created record."""
    # 1. Create User A and record
    user_a = User(
        username=f"usera_{uuid4().hex[:8]}",
        email=f"a_{uuid4().hex[:8]}@example.com",
        password_hash="hash",
    )
    db.add(user_a)
    db.commit()
    db.refresh(user_a)

    record_id = uuid4()
    sale = Sale(
        id=record_id,
        user_id=user_a.id,
        total_amount=100.0,
        invoice_number=f"INV-B-{uuid4().hex[:8]}",
    )
    db.add(sale)
    db.commit()

    # 2. User B tries to delete User A's record
    client_id_delete = str(uuid4())
    payload_delete = {
        "operations": [
            {
                "id": client_id_delete,
                "entity": "sale",
                "action": "delete",
                "payload": {
                    "id": str(record_id),
                    "customer_id": str(uuid4()),
                    "items": [],
                    "total_amount": 100.0,
                    "payment_method": "cash",
                },
                "updated_at": datetime.now().isoformat(),
            }
        ]
    }
    resp_delete = worker_client.post("/sync/push", json=payload_delete)
    assert resp_delete.status_code == 200
    failed = resp_delete.json()["failed"]
    assert len(failed) == 1
    assert failed[0]["client_id"] == client_id_delete
    assert "access denied" in failed[0]["error"].lower()
