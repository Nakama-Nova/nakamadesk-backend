"""
Tests for Production Job Management — Day 1 feature expansion.

Tests create_job, list/get jobs, and worker assignment/removal.
Also verifies DB linkage: job → order, assignment → job + worker.
Uses the same conftest fixtures as all other test modules.
"""

import uuid
from decimal import Decimal

from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _create_item(auth_client: TestClient) -> str:
    """Create a catalogue item, return its ID."""
    r = auth_client.post(
        "/items/",
        json={
            "sku": f"SKU-{uuid.uuid4().hex[:8]}",
            "name": "Teak Sculpture Base",
            "selling_price": 8000.0,
            "current_stock": 10,
        },
    )
    assert r.status_code in (200, 201), r.json()
    return r.json()["id"]


def _create_worker(auth_client: TestClient, role: str = "worker") -> str:
    """
    Create a system user with the given role, return their ID.

    Falls back to directly posting to /auth/register because the auth
    endpoint already creates the user in the DB and returns the user object.
    """
    r = auth_client.post(
        "/auth/register",
        json={
            "username": f"worker_{uuid.uuid4().hex[:8]}",
            "email": f"worker_{uuid.uuid4().hex[:8]}@test.com",
            "password": "testpassword123",
            "full_name": "Test Worker",
            "role": role,
        },
    )
    assert r.status_code in (200, 201), r.json()
    return r.json()["id"]


def _create_order(auth_client: TestClient, item_id: str) -> str:
    """Create a basic confirmed order, return its ID."""
    r = auth_client.post(
        "/orders/",
        json={
            "order_type": "standard",
            "items": [{"item_id": item_id, "quantity": 1, "unit_price": 8000}],
        },
    )
    assert r.status_code == 201, r.json()
    order_id = r.json()["id"]
    # Confirm the order
    auth_client.patch(f"/orders/{order_id}/status", json={"status": "confirmed"})
    return order_id


# ---------------------------------------------------------------------------
# Job creation — positive paths
# ---------------------------------------------------------------------------


def test_create_job_linked_to_order(auth_client: TestClient):
    """A production job linked to an order and a catalogue item should persist correctly."""
    item_id = _create_item(auth_client)
    order_id = _create_order(auth_client, item_id)

    payload = {
        "order_id": order_id,
        "item_id": item_id,
        "target_quantity": 1,
        "expected_by": "2026-06-30",
        "notes": "Priority order for temple",
    }
    r = auth_client.post("/production/jobs", json=payload)
    assert r.status_code == 201, r.json()

    data = r.json()
    assert data["order_id"] == order_id
    assert data["item_id"] == item_id
    assert data["status"] == "pending"
    assert data["target_quantity"] == 1
    assert Decimal(data["produced_quantity"]) == Decimal("0")
    assert data["job_number"].startswith("JOB-")
    assert data["worker_assignments"] == []


def test_create_standalone_job_with_custom_desc(auth_client: TestClient):
    """A standalone job (no order) for a bespoke piece should work."""
    payload = {
        "custom_desc": "6x3 ft teak door panel with arch carving",
        "target_quantity": 2,
        "expected_by": "2026-07-15",
    }
    r = auth_client.post("/production/jobs", json=payload)
    assert r.status_code == 201, r.json()

    data = r.json()
    assert data["order_id"] is None
    assert data["item_id"] is None
    assert data["custom_desc"] == "6x3 ft teak door panel with arch carving"
    assert data["status"] == "pending"


def test_job_number_is_sequential(auth_client: TestClient):
    """Sequential jobs should get incrementing numbers."""
    item_id = _create_item(auth_client)

    def make():
        return auth_client.post(
            "/production/jobs",
            json={"item_id": item_id, "target_quantity": 1},
        )

    r1 = make()
    r2 = make()
    assert r1.status_code == 201
    assert r2.status_code == 201

    n1 = int(r1.json()["job_number"].split("-")[-1])
    n2 = int(r2.json()["job_number"].split("-")[-1])
    assert n2 == n1 + 1


# ---------------------------------------------------------------------------
# Job creation — validation / edge cases
# ---------------------------------------------------------------------------


def test_create_job_rejects_missing_item_identity(auth_client: TestClient):
    """Must reject when neither item_id nor custom_desc is provided."""
    r = auth_client.post(
        "/production/jobs",
        json={"target_quantity": 1},
    )
    assert r.status_code == 400
    assert (
        "item_id" in r.json()["detail"].lower()
        or "custom_desc" in r.json()["detail"].lower()
    )


def test_create_job_rejects_nonexistent_order(auth_client: TestClient):
    """Should 404 when order_id does not exist."""
    r = auth_client.post(
        "/production/jobs",
        json={
            "order_id": str(uuid.uuid4()),
            "custom_desc": "Chair",
            "target_quantity": 1,
        },
    )
    assert r.status_code == 404


def test_create_job_rejects_nonexistent_item(auth_client: TestClient):
    """Should 404 when item_id does not exist in catalogue."""
    r = auth_client.post(
        "/production/jobs",
        json={"item_id": str(uuid.uuid4()), "target_quantity": 1},
    )
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Retrieval
# ---------------------------------------------------------------------------


def test_list_and_get_production_job(auth_client: TestClient):
    """Created job should appear in list and be fetchable by ID."""
    item_id = _create_item(auth_client)
    r = auth_client.post(
        "/production/jobs",
        json={"item_id": item_id, "target_quantity": 3},
    )
    assert r.status_code == 201
    job_id = r.json()["id"]

    # List
    list_r = auth_client.get("/production/jobs")
    assert list_r.status_code == 200
    job_ids = [j["id"] for j in list_r.json()]
    assert job_id in job_ids

    # Get by ID
    get_r = auth_client.get(f"/production/jobs/{job_id}")
    assert get_r.status_code == 200
    assert get_r.json()["id"] == job_id


def test_get_nonexistent_job_returns_404(auth_client: TestClient):
    r = auth_client.get(f"/production/jobs/{uuid.uuid4()}")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Worker assignment
# ---------------------------------------------------------------------------


def test_assign_worker_to_job(auth_client: TestClient, db):
    """Assigning a valid worker to a job should create the assignment record."""
    item_id = _create_item(auth_client)
    r_job = auth_client.post(
        "/production/jobs",
        json={"item_id": item_id, "target_quantity": 1},
    )
    job_id = r_job.json()["id"]

    # Create a worker user in DB directly (no full auth roundtrip needed)
    from app.models.user import User

    worker = User(
        username=f"carver_{uuid.uuid4().hex[:8]}",
        email=f"carver_{uuid.uuid4().hex[:8]}@vriksha.com",
        password_hash="fakehash",
        role="worker",
        is_active=True,
    )
    db.add(worker)
    db.commit()
    db.refresh(worker)
    worker_id = str(worker.id)

    # Assign
    r_assign = auth_client.post(
        f"/production/jobs/{job_id}/assign-worker",
        json={"worker_id": worker_id, "role": "sculptor"},
    )
    assert r_assign.status_code == 201, r_assign.json()

    data = r_assign.json()
    assert data["job_id"] == job_id
    assert data["worker_id"] == worker_id
    assert data["role"] == "sculptor"
    assert data["removed_at"] is None

    # Assignment should appear on the job
    job_r = auth_client.get(f"/production/jobs/{job_id}")
    assignments = job_r.json()["worker_assignments"]
    assert len(assignments) == 1
    assert assignments[0]["worker_id"] == worker_id


def test_duplicate_assignment_raises_400(auth_client: TestClient, db):
    """Assigning the same worker twice (while still active) must return 400."""
    from app.models.user import User

    item_id = _create_item(auth_client)
    r_job = auth_client.post(
        "/production/jobs",
        json={"item_id": item_id, "target_quantity": 1},
    )
    job_id = r_job.json()["id"]

    worker = User(
        username=f"dup_{uuid.uuid4().hex[:8]}",
        email=f"dup_{uuid.uuid4().hex[:8]}@vriksha.com",
        password_hash="fakehash",
        role="worker",
        is_active=True,
    )
    db.add(worker)
    db.commit()
    worker_id = str(worker.id)

    auth_client.post(
        f"/production/jobs/{job_id}/assign-worker",
        json={"worker_id": worker_id, "role": "carpenter"},
    )
    r2 = auth_client.post(
        f"/production/jobs/{job_id}/assign-worker",
        json={"worker_id": worker_id, "role": "carpenter"},
    )
    assert r2.status_code == 400
    assert "already assigned" in r2.json()["detail"].lower()


def test_remove_and_reassign_worker(auth_client: TestClient, db):
    """Removed worker can be re-assigned (reactivation, not duplicate)."""
    from app.models.user import User

    item_id = _create_item(auth_client)
    r_job = auth_client.post(
        "/production/jobs",
        json={"item_id": item_id, "target_quantity": 1},
    )
    job_id = r_job.json()["id"]

    worker = User(
        username=f"reactivate_{uuid.uuid4().hex[:8]}",
        email=f"reactivate_{uuid.uuid4().hex[:8]}@vriksha.com",
        password_hash="fakehash",
        role="worker",
        is_active=True,
    )
    db.add(worker)
    db.commit()
    worker_id = str(worker.id)

    # Assign
    auth_client.post(
        f"/production/jobs/{job_id}/assign-worker",
        json={"worker_id": worker_id, "role": "polisher"},
    )

    # Remove
    r_remove = auth_client.delete(f"/production/jobs/{job_id}/workers/{worker_id}")
    assert r_remove.status_code == 200
    assert r_remove.json()["removed_at"] is not None

    # Re-assign — should succeed (reactivation)
    r_reassign = auth_client.post(
        f"/production/jobs/{job_id}/assign-worker",
        json={"worker_id": worker_id, "role": "carpenter"},
    )
    assert r_reassign.status_code == 201
    assert r_reassign.json()["removed_at"] is None
    assert r_reassign.json()["role"] == "carpenter"


def test_assign_nonexistent_worker_returns_404(auth_client: TestClient):
    """Should 404 when worker_id does not exist."""
    item_id = _create_item(auth_client)
    r_job = auth_client.post(
        "/production/jobs",
        json={"item_id": item_id, "target_quantity": 1},
    )
    job_id = r_job.json()["id"]

    r = auth_client.post(
        f"/production/jobs/{job_id}/assign-worker",
        json={"worker_id": str(uuid.uuid4()), "role": "carpenter"},
    )
    assert r.status_code == 404
