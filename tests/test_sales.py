import pytest
from fastapi.testclient import TestClient

def test_create_item_and_sale(auth_client: TestClient):
    # Step 1: Create an item
    item_payload = {
        "sku": "SALE-ITEM-001",
        "name": "Selling Item",
        "category_id": None,
        "selling_price": 50.0,
        "current_stock": 10
    }
    item_response = auth_client.post("/items/", json=item_payload)
    assert item_response.status_code == 200
    item_id = item_response.json()["id"]
    
    # Step 2: Create a sale
    sale_payload = {
        "items": [
            {
                "item_id": item_id,
                "quantity": 3
            }
        ]
    }
    sale_response = auth_client.post("/sales/", json=sale_payload)
    assert sale_response.status_code == 200
    assert sale_response.json()["total_amount"] == 150.0  # 3 * 50
    
    # Step 3: Verify the stock is reduced to 7
    verify_item_response = auth_client.get(f"/items/{item_id}")
    assert verify_item_response.status_code == 200
    assert verify_item_response.json()["current_stock"] == 7

def test_list_sales(auth_client: TestClient):
    response = auth_client.get("/sales/")
    assert response.status_code == 200
    assert isinstance(response.json(), list)

def test_fetch_specific_sale(auth_client: TestClient):
    # Create a sale first
    item_payload = {
        "sku": "SALE-ITEM-002",
        "name": "Specific Sale Item",
        "selling_price": 100.0,
        "current_stock": 5
    }
    item_id = auth_client.post("/items/", json=item_payload).json()["id"]
    
    sale_payload = {"items": [{"item_id": item_id, "quantity": 1}]}
    sale_id = auth_client.post("/sales/", json=sale_payload).json()["id"]
    
    response = auth_client.get(f"/sales/{sale_id}")
    assert response.status_code == 200
    assert response.json()["id"] == sale_id
