from fastapi.testclient import TestClient


def test_invoice_generation_flow(auth_client: TestClient):
    # 1. Create an Item
    item_payload = {
        "sku": "INV-ITEM-001",
        "name": "Dining Table 1",
        "category_id": None,
        "selling_price": 10000,
        "current_stock": 10,
        "hsn_code": "9403",
        "gst_percent": 18.0,
    }
    response = auth_client.post("/items/", json=item_payload)
    assert response.status_code == 200
    item_id = response.json()["id"]

    # 2. Create a Customer
    customer_payload = {
        "name": "Ramesh",
        "email": "ramesh@example.com",
        "phone": "9876543210",
        "address": "Bangalore",
    }
    response = auth_client.post("/customers/", json=customer_payload)
    assert response.status_code == 200
    customer_id = response.json()["id"]

    # 3. Create a Sale
    sale_payload = {
        "customer_id": customer_id,
        "items": [{"item_id": item_id, "quantity": 1}],
    }
    response = auth_client.post("/sales/", json=sale_payload)
    assert response.status_code == 200
    sale_data = response.json()

    # 4. Verify invoice_number exists on sale response
    invoice_number = sale_data.get("invoice_number")
    assert invoice_number is not None
    assert invoice_number.startswith("NTD-")

    # 5. Verify GET /invoices returns list
    response = auth_client.get("/invoices/")
    assert response.status_code == 200
    invoices_list = response.json()
    assert len(invoices_list) > 0
    assert any(inv["invoice_number"] == invoice_number for inv in invoices_list)

    # 6. Verify GET /invoices/{invoice_number}
    response = auth_client.get(f"/invoices/{invoice_number}")
    assert response.status_code == 200
    invoice_data = response.json()
    assert invoice_data["invoice_number"] == invoice_number
    assert invoice_data["customer"]["name"] == "Ramesh"

    # 7. Verify GET /invoices/{invoice_number}/pdf
    response = auth_client.get(f"/invoices/{invoice_number}/pdf")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
