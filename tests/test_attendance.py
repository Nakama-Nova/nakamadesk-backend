import pytest
from fastapi.testclient import TestClient
from app.models.user import User
from datetime import date
from uuid import uuid4

def test_record_attendance_authorized(auth_client: TestClient, db):
    # Create a worker user to record attendance for
    unique_id = uuid4().hex[:8]
    worker = User(
        username=f"testworker_{unique_id}",
        email=f"testworker_{unique_id}@example.com",
        password_hash="fakehash",
        role="worker"
    )
    db.add(worker)
    db.commit()
    db.refresh(worker)
    
    payload = {
        "user_id": str(worker.id),
        "date": str(date.today()),
        "status": "present",
        "daily_wage": 1000.0
    }
    response = auth_client.post("/attendance/", json=payload)
    assert response.status_code == 200
    assert response.json()["status"] == "present"
    assert response.json()["daily_wage"] == "1000.00"

def test_record_attendance_unauthorized(worker_client: TestClient):
    payload = {
        "user_id": str(uuid4()),
        "date": str(date.today()),
        "status": "present",
        "daily_wage": 1000.0
    }
    response = worker_client.post("/attendance/", json=payload)
    assert response.status_code == 403 # Only owner/manager/sales can record

def test_list_attendance_filters(auth_client: TestClient):
    # Just verify the endpoint returns a list
    response = auth_client.get("/attendance/")
    assert response.status_code == 200
    assert isinstance(response.json(), list)

def test_update_attendance_status(auth_client: TestClient, db):
    # 1. Create worker
    unique_id = uuid4().hex[:8]
    worker = User(
        username=f"testworker_{unique_id}",
        email=f"testworker_{unique_id}@example.com",
        password_hash="fakehash",
        role="worker"
    )
    db.add(worker)
    db.commit()
    db.refresh(worker)

    # 2. Record attendance
    payload = {
        "user_id": str(worker.id),
        "date": str(date.today()),
        "status": "absent",
        "daily_wage": 1000.0
    }
    rec_resp = auth_client.post("/attendance/", json=payload)
    attendance_id = rec_resp.json()["id"]
    
    # 2. Update to half-day
    update_payload = {"status": "half-day"}
    upd_resp = auth_client.patch(f"/attendance/{attendance_id}", json=update_payload)
    assert upd_resp.status_code == 200
    assert upd_resp.json()["status"] == "half-day"
