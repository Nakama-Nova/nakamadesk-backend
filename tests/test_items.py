import pytest
from fastapi.testclient import TestClient

def test_create_item(auth_client: TestClient):
    payload = {
        "sku": "ITEM-TEST-001",
        "name": "Test Chair",
        "category_id": None,
        "purchase_price": 100.0,
        "selling_price": 200.0,
        "current_stock": 20,
        "min_stock": 5
    }
    response = auth_client.post("/items/", json=payload)
    assert response.status_code == 200
    assert response.json()["name"] == "Test Chair"

def test_update_item_stock(auth_client: TestClient):
    payload = {
        "sku": "ITEM-TEST-002",
        "name": "Stock Item",
        "current_stock": 20
    }
    create_resp = auth_client.post("/items/", json=payload)
    item_id = create_resp.json()["id"]
    
    # Adjust stock by -5
    response = auth_client.patch(f"/items/{item_id}/stock", json={"quantity": -5})
    assert response.status_code == 200
    assert response.json()["current_stock"] == 15

def test_negative_stock_reject(auth_client: TestClient):
    payload = {
        "sku": "ITEM-TEST-003",
        "name": "Negative Stock Item",
        "current_stock": 5
    }
    create_resp = auth_client.post("/items/", json=payload)
    item_id = create_resp.json()["id"]
    
    # Try deducting 10 from 5
    response = auth_client.patch(f"/items/{item_id}/stock", json={"quantity": -10})
    assert response.status_code == 400
    assert "Stock cannot go below zero" in response.json()["detail"]

def test_low_stock_flag(auth_client: TestClient):
    payload = {
        "sku": "ITEM-TEST-004",
        "name": "Low Stock Test",
        "current_stock": 6,
        "min_stock": 5
    }
    create_resp = auth_client.post("/items/", json=payload)
    item_id = create_resp.json()["id"]
    
    auth_client.patch(f"/items/{item_id}/stock", json={"quantity": -2})
    
    get_resp = auth_client.get(f"/items/{item_id}")
    assert get_resp.status_code == 200
    item = get_resp.json()
    assert item["current_stock"] == 4
    # Optional logic: the endpoint could expose "is_low_stock" flag directly or just verify numbers
    assert item["current_stock"] < item["min_stock"]

def test_list_items(auth_client: TestClient):
    response = auth_client.get("/items/")
    assert response.status_code == 200
    assert isinstance(response.json(), list)

def test_update_item(auth_client: TestClient):
    payload = {
        "sku": "ITEM-TEST-005",
        "name": "Update Target",
        "purchase_price": 50.0
    }
    create_resp = auth_client.post("/items/", json=payload)
    item_id = create_resp.json()["id"]
    
    update_payload = {"name": "Updated Target"}
    response = auth_client.put(f"/items/{item_id}", json=update_payload)
    assert response.status_code == 200
    assert response.json()["name"] == "Updated Target"
    assert response.json()["purchase_price"] == 50.0 # Unchanged
