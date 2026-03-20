from fastapi.testclient import TestClient
import concurrent.futures
import uuid
from app.db.deps import get_db
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import QueuePool
from app.core.config import settings


def _setup_thread_safe_db(client: TestClient):
    engine = create_engine(
        settings.DATABASE_URL, poolclass=QueuePool, pool_size=60, max_overflow=20
    )
    SafeLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_db():
        session = SafeLocal()
        try:
            yield session
        finally:
            session.close()

    client.app.dependency_overrides[get_db] = override_get_db


def test_stock_race_condition(auth_client: TestClient):
    _setup_thread_safe_db(auth_client)
    # Setup item with 10 stock
    customer_id = auth_client.post(
        "/customers/", json={"name": "Race Guy", "phone": f"888{uuid.uuid4().hex[:7]}"}
    ).json()["id"]
    item_id = auth_client.post(
        "/items/",
        json={
            "sku": f"RACE-{uuid.uuid4().hex[:4]}",
            "name": "Race Item",
            "selling_price": 100,
            "current_stock": 10,
        },
    ).json()["id"]

    # We will attempt 15 concurrent purchases of 1 stock each.
    def make_purchase():
        return auth_client.post(
            "/sales/",
            json={
                "customer_id": customer_id,
                "items": [{"item_id": item_id, "quantity": 1}],
            },
        )

    success_count = 0
    fail_count = 0

    with concurrent.futures.ThreadPoolExecutor(max_workers=15) as executor:
        futures = [executor.submit(make_purchase) for _ in range(15)]
        for future in concurrent.futures.as_completed(futures):
            resp = future.result()
            if resp.status_code == 200:
                success_count += 1
            else:
                fail_count += 1

    # Max stock was 10. We expect EXACTLY 10 to succeed, and 5 to fail (insufficient stock)
    assert (
        success_count == 10
    ), f"Expected 10 successes, got {success_count}. Failures: {fail_count}"
    assert fail_count == 5

    # Verify final stock is exactly 0
    final_stock = auth_client.get(f"/items/{item_id}").json()["current_stock"]
    assert final_stock == 0


def test_idempotency_concurrency(auth_client: TestClient):
    _setup_thread_safe_db(auth_client)
    # Setup
    resp = auth_client.post(
        "/customers/",
        json={"name": "Idempotent Guy", "phone": f"999{uuid.uuid4().hex[:7]}"},
    )
    assert resp.status_code == 200, resp.text
    customer_id = resp.json()["id"]

    resp_item = auth_client.post(
        "/items/",
        json={
            "sku": f"IDEM-{uuid.uuid4().hex[:4]}",
            "name": "Idempotent Item",
            "selling_price": 50,
            "current_stock": 100,
        },
    )
    assert resp_item.status_code == 200, resp_item.text
    item_id = resp_item.json()["id"]

    client_id = str(uuid.uuid4())
    payload = {
        "customer_id": customer_id,
        "client_id": client_id,
        "items": [{"item_id": item_id, "quantity": 5}],
    }

    # Fire 10 exactly duplicate requests concurrently
    def fire_duplicate():
        return auth_client.post("/sales/", json=payload)

    responses = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(fire_duplicate) for _ in range(10)]
        for future in concurrent.futures.as_completed(futures):
            responses.append(future.result())

    # Under correct idempotency checks, ALL should return 200 (gracefully returning the exact same Sale object)
    assert all(r.status_code == 200 for r in responses)

    # Ensure all returned the same sale UUID
    sale_ids = {r.json()["id"] for r in responses}
    assert len(sale_ids) == 1

    # Stock should have exactly decreased by 5 total, not 50!
    final_stock = auth_client.get(f"/items/{item_id}").json()["current_stock"]
    assert final_stock == 95


def test_rapid_request_stability(auth_client: TestClient):
    _setup_thread_safe_db(auth_client)
    """Fire a burst of requests to just ensure no SQLAlchemy pool exhaustion or Deadlock occurs broadly."""

    def fetch_items():
        return auth_client.get("/items/")

    success = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
        futures = [executor.submit(fetch_items) for _ in range(50)]
        for future in concurrent.futures.as_completed(futures):
            if future.result().status_code == 200:
                success += 1

    assert success == 50
