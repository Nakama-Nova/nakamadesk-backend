import pytest
from fastapi.testclient import TestClient

def test_gst_sales_flow(auth_client: TestClient):
    # Step 1: Create an item with GST
    item_payload = {
        "sku": "GST-CHAIR-001",
        "name": "GST Test Chair",
        "category_id": None,
        "selling_price": 1000.0,
        "current_stock": 10,
        "hsn_code": "9403",
        "gst_percent": 18.0
    }
    item_response = auth_client.post("/items/", json=item_payload)
    assert item_response.status_code == 200
    item = item_response.json()
    item_id = item["id"]

    # Step 2: Create a sale with the GST-enabled item
    sale_payload = {
        "items": [
            {
                "item_id": item_id,
                "quantity": 2
            }
        ]
    }
    sale_response = auth_client.post("/sales/", json=sale_payload)
    assert sale_response.status_code == 200
    sale = sale_response.json()

    # Step 3: Validate GST calculations
    # base_price = 1000 × 2 = 2000
    # gst_amount = 2000 × 0.18 = 360
    # cgst = 180, sgst = 180
    # total_price = 2360
    assert len(sale["items"]) == 1
    sale_item = sale["items"][0]
    assert sale_item.get("cgst_amount") == 180.0
    assert sale_item.get("sgst_amount") == 180.0
    assert sale_item.get("total_price") == 2360.0
    assert sale.get("total_amount") == 2360.0

    # Step 4: Verify inventory was reduced
    stock_response = auth_client.get(f"/items/{item_id}")
    assert stock_response.status_code == 200
    assert stock_response.json()["current_stock"] == 8

def test_gst_stored_on_item(auth_client: TestClient):
    """Verify that hsn_code and gst_percent are persisted correctly."""
    item_payload = {
        "sku": "GST-CHAIR-002",
        "name": "HSN Verify Item",
        "selling_price": 500.0,
        "current_stock": 5,
        "hsn_code": "4407",
        "gst_percent": 12.0
    }
    create_resp = auth_client.post("/items/", json=item_payload)
    assert create_resp.status_code == 200
    item_id = create_resp.json()["id"]

    fetch_resp = auth_client.get(f"/items/{item_id}")
    assert fetch_resp.status_code == 200
    fetched = fetch_resp.json()
    assert fetched.get("hsn_code") == "4407"
    assert fetched.get("gst_percent") == 12.0

def test_gst_zero_percent(auth_client: TestClient):
    """Items with 0% GST should produce zero tax and no inflated total."""
    item_payload = {
        "sku": "GST-CHAIR-003",
        "name": "Zero GST Item",
        "selling_price": 200.0,
        "current_stock": 5,
        "hsn_code": "0000",
        "gst_percent": 0.0
    }
    item_resp = auth_client.post("/items/", json=item_payload)
    assert item_resp.status_code == 200
    item_id = item_resp.json()["id"]

    sale_payload = {"items": [{"item_id": item_id, "quantity": 1}]}
    sale_resp = auth_client.post("/sales/", json=sale_payload)
    assert sale_resp.status_code == 200

    sale = sale_resp.json()
    sale_item = sale["items"][0]
    assert sale_item.get("cgst_amount") == 0.0
    assert sale_item.get("sgst_amount") == 0.0
    assert sale_item.get("total_price") == 200.0
    assert sale.get("total_amount") == 200.0
