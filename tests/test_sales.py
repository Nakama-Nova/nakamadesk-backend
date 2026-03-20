from fastapi.testclient import TestClient
import uuid


def test_create_sale_positive_flow(auth_client: TestClient):
    # Step 1: Create an item with GST
    item_payload = {
        "sku": "GST-CHAIR-001",
        "name": "GST Test Chair",
        "selling_price": 1000.0,
        "current_stock": 10,
        "hsn_code": "9403",
        "gst_percent": 18.0,
    }
    item_id = auth_client.post("/items/", json=item_payload).json()["id"]

    # Step 2: Create a sale with the GST-enabled item
    sale_payload = {"items": [{"item_id": item_id, "quantity": 2}]}
    sale_response = auth_client.post("/sales/", json=sale_payload)
    assert sale_response.status_code == 200
    sale = sale_response.json()

    # Step 3: Validate GST calculations (1000 * 2 = 2000, 18% GST = 360, Total = 2360)
    sale_item = sale["items"][0]
    assert float(sale_item["cgst_amount"]) == 180.0
    assert float(sale_item["sgst_amount"]) == 180.0
    assert float(sale_item["total_price"]) == 2360.0
    assert float(sale["total_amount"]) == 2360.0

    # Step 4: Verify inventory was reduced
    stock_response = auth_client.get(f"/items/{item_id}")
    assert stock_response.json()["current_stock"] == 8


def test_sale_insufficient_stock(auth_client: TestClient):
    item_payload = {
        "sku": "LOW-STOCK-001",
        "name": "Low Stock Item",
        "selling_price": 100.0,
        "current_stock": 5,
    }
    item_id = auth_client.post("/items/", json=item_payload).json()["id"]

    sale_payload = {"items": [{"item_id": item_id, "quantity": 10}]}
    response = auth_client.post("/sales/", json=sale_payload)

    assert response.status_code == 400
    assert "Insufficient stock" in response.json()["detail"]


def test_sale_invalid_item(auth_client: TestClient):
    invalid_item_id = str(uuid.uuid4())
    sale_payload = {"items": [{"item_id": invalid_item_id, "quantity": 1}]}
    response = auth_client.post("/sales/", json=sale_payload)

    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


def test_sale_idempotency_duplicate_client_id(auth_client: TestClient):
    item_payload = {
        "sku": "IDEM-SALE",
        "name": "Idemp Test Item",
        "selling_price": 50.0,
        "current_stock": 10,
    }
    item_id = auth_client.post("/items/", json=item_payload).json()["id"]

    client_id = str(uuid.uuid4())
    sale_payload = {
        "client_id": client_id,
        "items": [{"item_id": item_id, "quantity": 1}],
    }

    # First request
    resp1 = auth_client.post("/sales/", json=sale_payload)
    assert resp1.status_code == 200
    sale_id1 = resp1.json()["id"]

    # Second request with same client_id
    resp2 = auth_client.post("/sales/", json=sale_payload)
    assert resp2.status_code == 200
    assert resp2.json()["id"] == sale_id1

    # Stock should only be deducted once
    stock_response = auth_client.get(f"/items/{item_id}")
    assert stock_response.json()["current_stock"] == 9


def test_list_and_fetch_sale(auth_client: TestClient):
    item_payload = {
        "sku": "LIST-SALE-002",
        "name": "List Sale Item",
        "selling_price": 100.0,
        "current_stock": 5,
    }
    item_id = auth_client.post("/items/", json=item_payload).json()["id"]

    sale_payload = {"items": [{"item_id": item_id, "quantity": 1}]}
    sale_id = auth_client.post("/sales/", json=sale_payload).json()["id"]

    # List Sales
    list_resp = auth_client.get("/sales/")
    assert list_resp.status_code == 200
    assert len(list_resp.json()) > 0

    # Fetch Specific Sale
    fetch_resp = auth_client.get(f"/sales/{sale_id}")
    assert fetch_resp.status_code == 200
    assert fetch_resp.json()["id"] == sale_id

    # Ensure totals match decimal precision accurately
    assert float(fetch_resp.json()["total_amount"]) == 100.0
