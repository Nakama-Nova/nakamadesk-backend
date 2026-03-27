from fastapi.testclient import TestClient
import concurrent.futures
import uuid
from app.db.deps import get_db

from app.core.config import settings


from app.services.sales_service import create_sale_transaction
from app.repositories.sqlalchemy_repo import SQLAlchemyUnitOfWork
from app.schemas.sale import SaleCreate, SaleItemCreate
from sqlalchemy.orm import sessionmaker
from app.db.session import SessionLocal
from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool
import time
from uuid import UUID

# For concurrency tests, we use a separate engine with NullPool
# to ensure we don't hit pool exhaustion hangs
concurrency_engine = create_engine(settings.DATABASE_URL, poolclass=NullPool)
ConcurrencySessionLocal = sessionmaker(
    autocommit=False, autoflush=False, bind=concurrency_engine
)


def test_stock_race_condition(auth_client: TestClient):
    # Setup - use auth_client for easy setup (non-concurrent)
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

    cust_uuid = UUID(customer_id)
    item_uuid = UUID(item_id)
    # Get current user ID (staff)
    staff_id = UUID(auth_client.get("/auth/me").json()["id"])

    def make_purchase():
        # Each thread gets its OWN session and UoW from the concurrency-safe pool
        session = ConcurrencySessionLocal()
        uow = SQLAlchemyUnitOfWork(session)
        sale_data = SaleCreate(
            customer_id=cust_uuid, items=[SaleItemCreate(item_id=item_uuid, quantity=1)]
        )
        try:
            # Call service directly with isolated UoW
            create_sale_transaction(uow, sale_data, staff_id)
            return True
        except Exception:
            return False
        finally:
            session.close()

    success_count = 0
    fail_count = 0

    with concurrent.futures.ThreadPoolExecutor(max_workers=15) as executor:
        futures = [executor.submit(make_purchase) for _ in range(15)]
        for future in concurrent.futures.as_completed(futures):
            if future.result():
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
    # Setup
    customer_id = auth_client.post(
        "/customers/",
        json={"name": "Idempotent Guy", "phone": f"999{uuid.uuid4().hex[:7]}"},
    ).json()["id"]

    item_id = auth_client.post(
        "/items/",
        json={
            "sku": f"IDEM-{uuid.uuid4().hex[:4]}",
            "name": "Idempotent Item",
            "selling_price": 50,
            "current_stock": 100,
        },
    ).json()["id"]

    cust_uuid = UUID(customer_id)
    item_uuid = UUID(item_id)
    staff_id = UUID(auth_client.get("/auth/me").json()["id"])
    client_id = uuid.uuid4()

    sale_data = SaleCreate(
        customer_id=cust_uuid,
        client_id=client_id,
        items=[SaleItemCreate(item_id=item_uuid, quantity=5)],
    )

    def fire_duplicate():
        session = ConcurrencySessionLocal()
        uow = SQLAlchemyUnitOfWork(session)
        try:
            create_sale_transaction(uow, sale_data, staff_id)
            return True
        except Exception:
            return False
        finally:
            session.close()

    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(fire_duplicate) for _ in range(10)]
        for future in concurrent.futures.as_completed(futures):
            results.append(future.result())

    # Under correct idempotency checks, ALL should return success (sharing the same Sale object)
    assert all(results), "Not all idempotent requests succeeded"

    # Stock should have exactly decreased by 5 total
    final_stock = auth_client.get(f"/items/{item_id}").json()["current_stock"]
    assert final_stock == 95


def test_rapid_request_stability(auth_client: TestClient):
    """Fire a burst of requests hitting the API directly but with isolated clients if possible."""

    def fetch_items():
        # Fresh client per thread hitting the app
        with TestClient(auth_client.app) as local_client:
            local_client.headers = auth_client.headers
            return local_client.get("/items/")

    success = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
        futures = [executor.submit(fetch_items) for _ in range(50)]
        for future in concurrent.futures.as_completed(futures):
            resp = future.result()
            if resp.status_code == 200:
                success += 1

    assert success == 50
