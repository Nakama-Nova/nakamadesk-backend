import pytest
from fastapi.testclient import TestClient
import concurrent.futures
import time
import uuid
import statistics

# Reusing the nullpool thread safe db from earlier
from tests.test_concurrency import _setup_thread_safe_db

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
    
    # Assert acceptable performance thresholds
    assert avg < 0.5, f"Avg response time too high: {avg}s"
    assert p95 < 1.0, f"P95 latency too high: {p95}s"

def test_load_sales_api(auth_client: TestClient):
    """Stress test the core Sales creation endpoint."""
    _setup_thread_safe_db(auth_client)
    
    # Setup master constraints
    customer_id = auth_client.post("/customers/", json={"name": "Load Test Customer", "phone": f"111{uuid.uuid4().hex[:7]}"}).json()["id"]
    item_id = auth_client.post("/items/", json={
        "sku": f"LOAD-{uuid.uuid4().hex[:4]}", "name": "Bulk Item", "selling_price": 50, "current_stock": 10000
    }).json()["id"]
    
    latencies = []
    
    def fire_sale():
        payload = {
            "customer_id": customer_id, 
            "items": [{"item_id": item_id, "quantity": 1}]
        }
        start = time.time()
        resp = auth_client.post("/sales/", json=payload)
        t = time.time() - start
        if resp.status_code == 200:
            return t
        return None

    # Simulate 200 requests representing moderate load spikes
    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
        futures = [executor.submit(fire_sale) for _ in range(200)]
        for future in concurrent.futures.as_completed(futures):
            res = future.result()
            if res is not None:
                latencies.append(res)
                
    assert len(latencies) > 180, "Too many failed requests during load test"
    _track_metrics(latencies, "Sales Create API")
