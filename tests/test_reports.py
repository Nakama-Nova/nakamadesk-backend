import pytest
from fastapi.testclient import TestClient
from datetime import date
from uuid import uuid4

def test_get_sales_report(auth_client: TestClient):
    response = auth_client.get("/reports/sales")
    assert response.status_code == 200
    data = response.json()
    assert "total_sales" in data
    assert "total_revenue" in data

def test_get_top_products(auth_client: TestClient):
    response = auth_client.get("/reports/top-products?limit=5")
    assert response.status_code == 200
    assert isinstance(response.json(), list)

def test_get_inventory_report(auth_client: TestClient):
    response = auth_client.get("/reports/inventory")
    assert response.status_code == 200
    assert isinstance(response.json(), list)

def test_get_profit_loss(auth_client: TestClient):
    response = auth_client.get("/reports/profit-loss")
    assert response.status_code == 200
    data = response.json()
    assert "net_profit" in data
    assert "profit_margin_pct" in data

def test_get_gst_summary(auth_client: TestClient):
    response = auth_client.get("/reports/gst-summary")
    assert response.status_code == 200
    data = response.json()
    assert "taxable_value" in data
    assert "cgst_total" in data

def test_export_sales_excel(auth_client: TestClient):
    response = auth_client.get("/reports/export/sales")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

def test_reports_rbac_worker(worker_client: TestClient):
    # Workers should NOT be able to access reports
    response = worker_client.get("/reports/sales")
    assert response.status_code == 403
