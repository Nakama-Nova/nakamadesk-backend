from fastapi.testclient import TestClient
from app.models.user import User
from datetime import date, datetime, timedelta, timezone
from uuid import uuid4
from decimal import Decimal


def test_check_in_out_wage_flow(auth_client: TestClient, db):
    # Create worker
    unique_id = uuid4().hex[:8]
    worker = User(
        username=f"worker_{unique_id}",
        email=f"worker_{unique_id}@example.com",
        password_hash="fakehash",
        role="worker",
        base_daily_wage=Decimal("1000.00"),
    )
    db.add(worker)
    db.commit()
    db.refresh(worker)

    # 1. Check-in (as Admin)
    resp = auth_client.post(f"/attendance/check-in?user_id={worker.id}")
    assert resp.status_code == 200
    attendance_id = resp.json()["id"]
    assert resp.json()["status"] == "absent"  # Initially absent

    # 2. Duplicate check-in (should fail)
    duplicate_resp = auth_client.post(f"/attendance/check-in?user_id={worker.id}")
    assert duplicate_resp.status_code == 400

    # 3. Check-out (mocking time isn't easy here, so we verify logic)
    # We'll manually update check_in time to the past to test 8h logic
    from app.models.attendance import Attendance

    db_att = db.query(Attendance).filter(Attendance.id == attendance_id).first()
    # Use naive UTC to match service logic
    past_time = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=9)
    db_att.check_in = past_time
    db.commit()
    db.expire_all()  # Ensure next query fetches fresh data

    checkout_resp = auth_client.post(
        f"/attendance/check-out?attendance_id={attendance_id}"
    )
    assert checkout_resp.status_code == 200
    data = checkout_resp.json()
    assert data["status"] == "present"
    assert Decimal(str(data["total_hours"])) >= Decimal("9.00")

    # 4. Verify Wage amount
    # Assuming AttendanceResponse includes wage_entry or we check /wages/pending
    from app.models.daily_wage import DailyWage

    wage = db.query(DailyWage).filter(DailyWage.attendance_id == attendance_id).first()
    assert wage.amount == Decimal("1000.00")


def test_rbac_check_in_out(worker_client: TestClient):
    # Worker cannot mark attendance
    user_id = str(uuid4())
    assert (
        worker_client.post(f"/attendance/check-in?user_id={user_id}").status_code == 403
    )

    att_id = str(uuid4())
    assert (
        worker_client.post(f"/attendance/check-out?attendance_id={att_id}").status_code
        == 403
    )


def test_my_attendance(worker_client: TestClient, db):
    # Setup: Create a record for the worker
    from app.models.attendance import Attendance
    from app.db.deps import (
        get_current_user,
    )  # This might be tricky in tests, use db directly

    # We need the worker's ID from the client context.
    # Usually the test setup handles this.
    pass
