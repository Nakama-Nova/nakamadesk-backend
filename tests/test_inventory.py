import pytest
from fastapi.testclient import TestClient

def test_create_item(auth_client: TestClient):
    payload = {
        "sku": "ITEM-001",
        "name": "Test Chair",
        "category": "Furniture",
        "purchase_price": 100.0,
        "selling_price": 200.0,
        "current_stock": 20
    }
    response = auth_client.post("/items/", json=payload)
    assert response.status_code == 200
    assert response.json()["name"] == "Test Chair"

def test_list_items(auth_client: TestClient):
    response = auth_client.get("/items/")
    assert response.status_code == 200
    assert isinstance(response.json(), list)

def test_fetch_item_by_id(auth_client: TestClient):
    # Create item first
    payload = {
        "sku": "ITEM-002",
        "name": "Another Chair",
        "purchase_price": 100.0,
        "selling_price": 200.0
    }
    create_response = auth_client.post("/items/", json=payload)
    item_id = create_response.json()["id"]
    
    response = auth_client.get(f"/items/{item_id}")
    assert response.status_code == 200
    assert response.json()["id"] == item_id

def test_update_item(auth_client: TestClient):
    # Create item
    payload = {
        "sku": "ITEM-003",
        "name": "Update Me",
        "purchase_price": 100.0,
        "selling_price": 200.0
    }
    create_response = auth_client.post("/items/", json=payload)
    item_id = create_response.json()["id"]
    
    update_payload = {
        "name": "Updated Name",
        "selling_price": 250.0
    }
    response = auth_client.put(f"/items/{item_id}", json=update_payload)
    assert response.status_code == 200
    assert response.json()["name"] == "Updated Name"
    assert response.json()["selling_price"] == 250.0

def test_adjust_stock(auth_client: TestClient):
    # Create item with stock 20
    payload = {
        "sku": "ITEM-004",
        "name": "Stock Item",
        "current_stock": 20
    }
    create_response = auth_client.post("/items/", json=payload)
    item_id = create_response.json()["id"]
    
    # Adjust stock by -5
    response = auth_client.patch(f"/items/{item_id}/stock", json={"quantity": -5})
    assert response.status_code == 200
    assert response.json()["current_stock"] == 15

def test_delete_item(auth_client: TestClient):
    # Create item
    payload = {
        "sku": "ITEM-005",
        "name": "Delete Me"
    }
    create_response = auth_client.post("/items/", json=payload)
    item_id = create_response.json()["id"]
    
    response = auth_client.delete(f"/items/{item_id}")
    assert response.status_code == 200
    
    # Verify deletion
    get_response = auth_client.get(f"/items/{item_id}")
    assert get_response.status_code == 404
