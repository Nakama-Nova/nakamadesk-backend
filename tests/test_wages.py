import pytest
from fastapi.testclient import TestClient
from datetime import date
from uuid import uuid4
from decimal import Decimal

from app.models.user import User

def test_wage_calculation_on_record(auth_client: TestClient, db):
    # 1. Create worker
    unique_id = uuid4().hex[:8]
    worker = User(
        username=f"worker_{unique_id}",
        email=f"worker_{unique_id}@example.com",
        password_hash="fakehash",
        role="worker"
    )
    db.add(worker)
    db.commit()
    db.refresh(worker)

    # 2. Record half-day attendance
    payload = {
        "user_id": str(worker.id),
        "date": str(date.today()),
        "status": "half-day",
        "daily_wage": 800.0
    }
    auth_client.post("/attendance/", json=payload)
    
    # 2. Check pending wages
    response = auth_client.get(f"/wages/pending?user_id={worker.id}")
    assert response.status_code == 200
    wages = response.json()
    assert len(wages) == 1
    # 50% of 800 = 400
    assert Decimal(str(wages[0]["total_amount"])) == Decimal("400.00")

def test_pay_wages_flow(auth_client: TestClient, db):
    # 1. Create worker
    unique_id = uuid4().hex[:8]
    worker = User(
        username=f"worker_pay_{unique_id}",
        email=f"worker_pay_{unique_id}@example.com",
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
        "status": "present",
        "daily_wage": 1200.0
    }
    rec_resp = auth_client.post("/attendance/", json=payload)
    attendance_id = rec_resp.json()["id"]
    
    # 2. Pay wages
    pay_payload = {
        "attendance_ids": [attendance_id],
        "transaction_ref": "TXN12345"
    }
    pay_resp = auth_client.post("/wages/pay", json=pay_payload)
    assert pay_resp.status_code == 200
    assert pay_resp.json()[0]["payment_status"] == "paid"
    assert pay_resp.json()[0]["transaction_ref"] == "TXN12345"
    
    # 3. Verify no more pending
    pend_resp = auth_client.get(f"/wages/pending?user_id={worker.id}")
    assert len(pend_resp.json()) == 0

def test_worker_cannot_pay_wages(worker_client: TestClient):
    pay_payload = {
        "attendance_ids": [str(uuid4())],
        "transaction_ref": "ILLEGAL"
    }
    response = worker_client.post("/wages/pay", json=pay_payload)
    assert response.status_code == 403
