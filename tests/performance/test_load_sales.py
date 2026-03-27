import concurrent.futures
import time
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings

# Reusing the nullpool thread safe db from earlier
load_engine = create_engine(settings.DATABASE_URL, poolclass=NullPool)


def _track_metrics(latencies, name="API"):
    if not latencies:
        print(f"\n[{name}] No successful requests to measure.")
        return

    avg = sum(latencies) / len(latencies)
    p95 = sorted(latencies)[int(len(latencies) * 0.95)]
    throughput = len(latencies) / sum(latencies) if sum(latencies) > 0 else 0
    print(f"\n--- {name} LOAD TEST METRICS ---")
    print(f"Total Requests: {len(latencies)}")
    print(f"Avg Response Time: {avg*1000:.2f} ms")
    print(f"P95 Latency: {p95*1000:.2f} ms")
    print(f"Est. Throughput: {throughput:.2f} req/sec limit per thread map")

    # Assert acceptable performance thresholds for heavy writes
    assert avg < 0.500, f"Avg response time too high: {avg}s"
    assert p95 < 1.000, f"P95 latency too high: {p95}s"


def test_load_sales_api(auth_client: TestClient):
    """Stress test the core Sales creation endpoint."""

    # Setup master constraints using the main client
    customer_id = auth_client.post(
        "/customers/",
        json={"name": "Load Test Customer", "phone": f"111{uuid.uuid4().hex[:7]}"},
    ).json()["id"]
    item_id = auth_client.post(
        "/items/",
        json={
            "sku": f"LOAD-{uuid.uuid4().hex[:4]}",
            "name": "Bulk Item",
            "selling_price": 50,
            "current_stock": 10000,
        },
    ).json()["id"]

    latencies = []

    def run_worker(request_count):
        # Fresh client per thread hitting the app directly
        with TestClient(auth_client.app) as local_client:
            local_client.headers = auth_client.headers
            worker_latencies = []
            for _ in range(request_count):
                payload = {
                    "customer_id": customer_id,
                    "items": [{"item_id": item_id, "quantity": 1}],
                }
                start = time.time()
                resp = local_client.post("/sales/", json=payload)
                t = time.time() - start
                if resp.status_code == 200:
                    worker_latencies.append(t)
                else:
                    print(f"FAILED SALE: {resp.text}")
            return worker_latencies

    # Simulate 200 requests representing moderate load spikes among 20 workers
    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
        futures = [executor.submit(run_worker, 10) for _ in range(20)]
        for future in concurrent.futures.as_completed(futures):
            res = future.result()
            if res:
                latencies.extend(res)

    assert (
        len(latencies) > 180
    ), f"Too many failed requests during load test: {200 - len(latencies)} failed"
    _track_metrics(latencies, "Sales Create API")
