import pytest
from fastapi.testclient import TestClient

def test_register_new_user(client: TestClient):
    payload = {
        "username": "newuser",
        "email": "newuser@example.com",
        "password": "testpassword123",
        "full_name": "New User",
        "phone": "1234567890"
    }
    response = client.post("/auth/register", json=payload)
    assert response.status_code == 200
    assert response.json()["username"] == "newuser"

def test_register_duplicate_user(client: TestClient):
    payload = {
        "username": "dupuser",
        "email": "dup@example.com",
        "password": "password123"
    }
    client.post("/auth/register", json=payload)
    
    # Try to register again with same username
    response = client.post("/auth/register", json=payload)
    assert response.status_code == 400

def test_login_valid_user(client: TestClient):
    # Register first
    register_payload = {
        "username": "loginuser",
        "email": "login@example.com",
        "password": "password123"
    }
    client.post("/auth/register", json=register_payload)
    
    # Login
    login_payload = {
        "username": "loginuser",
        "password": "password123"
    }
    response = client.post("/auth/login", data=login_payload)
    assert response.status_code == 200
    assert "access_token" in response.json()

def test_access_protected_endpoint(client: TestClient):
    # Register and login to get token
    register_payload = {
        "username": "protecteduser",
        "email": "protected@example.com",
        "password": "password123"
    }
    client.post("/auth/register", json=register_payload)
    
    login_response = client.post("/auth/login", data={"username": "protecteduser", "password": "password123"})
    token = login_response.json()["access_token"]
    
    headers = {"Authorization": f"Bearer {token}"}
    response = client.get("/auth/me", headers=headers)
    assert response.status_code == 200
    assert response.json()["username"] == "protecteduser"