import uuid

from fastapi.testclient import TestClient


def test_create_customer(auth_client: TestClient):
    payload = {
        "name": "Test Customer",
        "phone": f"9988776{uuid.uuid4().hex[:3]}",  # random phone
        "email": f"test_{uuid.uuid4().hex[:4]}@example.com",
        "address": "123 Test St",
        "gst_number": "22AAAAA0000A1Z5",
    }
    response = auth_client.post("/customers/", json=payload)
    assert response.status_code == 200
    assert response.json()["name"] == "Test Customer"


def test_create_customer_duplicate_phone(auth_client: TestClient):
    phone = f"6655443{uuid.uuid4().hex[:3]}"
    payload = {"name": "First Customer", "phone": phone}
    auth_client.post("/customers/", json=payload)

    # Duplicate phone
    payload2 = {"name": "Second Customer", "phone": phone}
    response = auth_client.post("/customers/", json=payload2)
    assert response.status_code == 400
    assert "already exists" in response.json()["detail"].lower()


def test_fetch_customer_list(auth_client: TestClient):
    response = auth_client.get("/customers/")
    assert response.status_code == 200
    assert isinstance(response.json(), list)


def test_update_customer(auth_client: TestClient):
    payload = {"name": "Update Target", "phone": f"8877665{uuid.uuid4().hex[:3]}"}
    create_resp = auth_client.post("/customers/", json=payload)
    customer_id = create_resp.json()["id"]

    update_payload = {"name": "Updated Name"}
    update_resp = auth_client.put(f"/customers/{customer_id}", json=update_payload)
    assert update_resp.status_code == 200
    assert update_resp.json()["name"] == "Updated Name"
    assert update_resp.json()["phone"] == payload["phone"]  # Unchanged


def test_invalid_customer_data(auth_client: TestClient):
    # Name is usually required
    payload = {"phone": "1231231234"}
    response = auth_client.post("/customers/", json=payload)
    assert response.status_code == 422  # Validation error
