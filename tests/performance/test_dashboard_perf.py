import concurrent.futures
import time

from fastapi.testclient import TestClient

from tests.performance.test_load_sales import _track_metrics


def test_dashboard_perf(auth_client: TestClient):
    """Stress test the dashboard analytical aggregation endpoints to isolate N+1 limits."""

    latencies = []

    def run_worker(request_count):
        # Fresh client per thread hitting the app directly
        with TestClient(auth_client.app) as local_client:
            local_client.headers = auth_client.headers
            worker_latencies = []
            for _ in range(request_count):
                start = time.time()
                resp = local_client.get("/dashboard/summary")
                t = time.time() - start
                if resp.status_code == 200:
                    worker_latencies.append(t)
                else:
                    print(f"FAILED DASHBOARD: {resp.text}")
            return worker_latencies

    # Simulate 100 concurrent admin/dashboard users attempting to render real-time analytics
    # We'll split 100 requests among 20 workers (5 each) to reduce startup overhead
    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
        futures = [executor.submit(run_worker, 5) for _ in range(20)]
        for future in concurrent.futures.as_completed(futures):
            res = future.result()
            if res:
                latencies.extend(res)

    assert (
        len(latencies) >= 90
    ), f"Too many dashboard analytical queries failed: {100 - len(latencies)} failed"
    _track_metrics(latencies, "Dashboard Analytics API")
