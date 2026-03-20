from fastapi.testclient import TestClient
from decimal import Decimal


def test_get_dashboard_summary(auth_client: TestClient):
    # Depending on prior tests, dashboard could have a lot of data.
    # We will just verify the schema and ensure numerical fields exist.

    response = auth_client.get("/dashboard/summary")
    assert response.status_code == 200
    data = response.json()

    assert "today_sales_count" in data
    assert isinstance(data["today_sales_count"], int)

    assert "today_revenue" in data
    assert Decimal(str(data["today_revenue"])) >= Decimal("0.00")

    assert "low_stock_count" in data
    assert isinstance(data["low_stock_count"], int)

    assert "top_products" in data
    assert isinstance(data["top_products"], list)

    assert "pending_wages_total" in data
    assert Decimal(str(data["pending_wages_total"])) >= Decimal("0.00")


def test_dashboard_unauthorized(worker_client: TestClient):
    response = worker_client.get("/dashboard/summary")
    assert response.status_code == 403
