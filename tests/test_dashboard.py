import pytest
from fastapi.testclient import TestClient

def test_get_dashboard_summary(auth_client: TestClient):
    response = auth_client.get("/dashboard/summary")
    assert response.status_code == 200
    data = response.json()
    assert "today_sales_count" in data
    assert "today_revenue" in data
    assert "low_stock_count" in data
    assert "top_products" in data
    assert "pending_wages_total" in data
