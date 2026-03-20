from fastapi.testclient import TestClient
import concurrent.futures
import time

from tests.test_concurrency import _setup_thread_safe_db
from tests.performance.test_load_sales import _track_metrics


def test_dashboard_perf(auth_client: TestClient):
    """Stress test the dashboard analytical aggregation endpoints to isolate N+1 limits."""
    _setup_thread_safe_db(auth_client)

    latencies = []

    def fire_dashboard():
        start = time.time()
        resp = auth_client.get("/dashboard/summary")
        t = time.time() - start
        if resp.status_code == 200:
            return t
        print(resp.text)
        return None

    # Simulate 100 concurrent admin/dashboard users attempting to render real-time analytics
    with concurrent.futures.ThreadPoolExecutor(max_workers=30) as executor:
        futures = [executor.submit(fire_dashboard) for _ in range(100)]
        for future in concurrent.futures.as_completed(futures):
            res = future.result()
            if res is not None:
                latencies.append(res)

    assert len(latencies) >= 90, "Too many dashboard analytical queries failed!"
    _track_metrics(latencies, "Dashboard Analytics API")
