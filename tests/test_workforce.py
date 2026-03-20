import pytest
from fastapi.testclient import TestClient
from app.models.user import User
from datetime import date
from uuid import uuid4
from decimal import Decimal

def test_record_attendance_and_wages(auth_client: TestClient, db):
    # Create worker
    unique_id = uuid4().hex[:8]
    worker = User(
        username=f"worker_{unique_id}", email=f"worker_{unique_id}@example.com",
        password_hash="fakehash", role="worker"
    )
    db.add(worker)
    db.commit()
    db.refresh(worker)
    
    # 1. Mark attendance
    client_id = str(uuid4())
    payload = {
        "user_id": str(worker.id),
        "date": str(date.today()),
        "status": "present",
        "daily_wage": 1000.0,
        "client_id": client_id
    }
    resp = auth_client.post("/attendance/", json=payload)
    assert resp.status_code == 200
    attendance_id = resp.json()["id"]
    
    # 2. Duplicate attendance / Idempotency check
    duplicate_resp = auth_client.post("/attendance/", json=payload)
    assert duplicate_resp.status_code == 200
    assert duplicate_resp.json()["id"] == attendance_id # Returns same record
    
    # 3. Check pending wages -> 1000.0
    wages_resp = auth_client.get(f"/wages/pending?user_id={worker.id}")
    wages = wages_resp.json()
    assert len(wages) == 1
    assert Decimal(str(wages[0]["total_amount"])) == Decimal("1000.00")
    
    # 4. Update attendance to half-day -> Wage recalculation
    update_payload = {"status": "half-day"}
    upd_resp = auth_client.patch(f"/attendance/{attendance_id}", json=update_payload)
    assert upd_resp.status_code == 200
    
    wages_resp_2 = auth_client.get(f"/wages/pending?user_id={worker.id}")
    wages2 = wages_resp_2.json()
    assert Decimal(str(wages2[0]["total_amount"])) == Decimal("500.00")
    
    # 5. Pay wages
    pay_payload = {
        "attendance_ids": [attendance_id],
        "transaction_ref": "TXN999"
    }
    pay_resp = auth_client.post("/wages/pay", json=pay_payload)
    assert pay_resp.status_code == 200
    assert pay_resp.json()[0]["payment_status"] == "paid"
    
def test_unauthorized_attendance_and_wages(worker_client: TestClient):
    payload = {
        "user_id": str(uuid4()),
        "date": str(date.today()),
        "status": "present",
        "daily_wage": 1000.0
    }
    assert worker_client.post("/attendance/", json=payload).status_code == 403
    
    pay_payload = {"attendance_ids": [str(uuid4())], "transaction_ref": "TXN000"}
    assert worker_client.post("/wages/pay", json=pay_payload).status_code == 403
