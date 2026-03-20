import pytest
from fastapi.testclient import TestClient
import concurrent.futures
import time
from datetime import date, timedelta

from tests.test_concurrency import _setup_thread_safe_db
from tests.performance.test_load_sales import _track_metrics

def test_load_reports(auth_client: TestClient):
    """Stress test the financial and analytical reporting layer evaluating sequential logic delays."""
    _setup_thread_safe_db(auth_client)
    
    start_str = (date.today() - timedelta(days=30)).isoformat()
    end_str = date.today().isoformat()
    
    latencies = []
    
    def fire_report():
        start = time.time()
        # Ping the Profit-Loss and Sales report natively
        resp = auth_client.get(f"/reports/profit-loss?start_date={start_str}&end_date={end_str}")
        t = time.time() - start
        
        if resp.status_code == 200:
            return t
        print(f"FAILED REPORT: {resp.text}")
        return None

    # Simulate 100 deep-reporting query traces
    with concurrent.futures.ThreadPoolExecutor(max_workers=30) as executor:
        futures = [executor.submit(fire_report) for _ in range(100)]
        for future in concurrent.futures.as_completed(futures):
            res = future.result()
            if res is not None:
                latencies.append(res)
                
    assert len(latencies) >= 90, "Multiple report API queries collapsed under load!"
    _track_metrics(latencies, "Profit-Loss Reports API")
