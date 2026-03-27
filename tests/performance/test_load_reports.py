import concurrent.futures
import time
from datetime import date, timedelta

from fastapi.testclient import TestClient

from tests.performance.test_load_sales import _track_metrics


def test_load_reports(auth_client: TestClient):
    """Stress test the financial and analytical reporting layer evaluating sequential logic delays."""

    start_str = (date.today() - timedelta(days=30)).isoformat()
    end_str = date.today().isoformat()

    latencies = []

    def run_worker(request_count):
        # Fresh client per thread hitting the app directly
        with TestClient(auth_client.app) as local_client:
            local_client.headers = auth_client.headers
            worker_latencies = []
            for _ in range(request_count):
                start = time.time()
                # Ping the Profit-Loss and Sales report natively
                resp = local_client.get(
                    f"/reports/profit-loss?start_date={start_str}&end_date={end_str}"
                )
                t = time.time() - start

                if resp.status_code == 200:
                    worker_latencies.append(t)
                else:
                    print(f"FAILED REPORT: {resp.text}")
            return worker_latencies

    # Simulate 100 deep-reporting query traces among 20 threads
    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
        futures = [executor.submit(run_worker, 5) for _ in range(20)]
        for future in concurrent.futures.as_completed(futures):
            res = future.result()
            if res:
                latencies.extend(res)

    assert (
        len(latencies) >= 90
    ), f"Multiple report API queries collapsed under load: {100 - len(latencies)} failed"
    _track_metrics(latencies, "Profit-Loss Reports API")
