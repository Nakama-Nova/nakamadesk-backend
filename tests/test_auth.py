from fastapi.testclient import TestClient


def test_register_new_user(client: TestClient):
    payload = {
        "username": "newuser",
        "email": "newuser@example.com",
        "password": "testpassword123",
        "full_name": "New User",
        "phone": "1234567890",
    }
    response = client.post("/auth/register", json=payload)
    assert response.status_code == 200
    assert response.json()["username"] == "newuser"


def test_register_duplicate_user(client: TestClient):
    payload = {
        "username": "dupuser",
        "email": "dup@example.com",
        "password": "password123",
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
        "password": "password123",
    }
    client.post("/auth/register", json=register_payload)

    # Login
    login_payload = {"username": "loginuser", "password": "password123"}
    response = client.post("/auth/login", data=login_payload)
    assert response.status_code == 200
    assert "access_token" in response.json()


def test_login_invalid_credentials(client: TestClient):
    login_payload = {"username": "nonexistentuser", "password": "wrongpassword"}
    response = client.post("/auth/login", data=login_payload)
    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid credentials"


def test_access_protected_endpoint(client: TestClient):
    # Register and login to get token
    register_payload = {
        "username": "protecteduser",
        "email": "protected@example.com",
        "password": "password123",
    }
    client.post("/auth/register", json=register_payload)

    login_response = client.post(
        "/auth/login", data={"username": "protecteduser", "password": "password123"}
    )
    token = login_response.json()["access_token"]

    headers = {"Authorization": f"Bearer {token}"}
    response = client.get("/auth/me", headers=headers)
    assert response.status_code == 200
    assert response.json()["username"] == "protecteduser"


def test_access_with_invalid_token(client: TestClient):
    headers = {"Authorization": "Bearer invalid_token"}
    response = client.get("/auth/me", headers=headers)
    assert response.status_code == 401


def test_access_with_expired_token(client: TestClient):
    from app.core.security import SECRET_KEY, ALGORITHM
    from jose import jwt
    from datetime import datetime, timedelta, timezone

    expire = datetime.now(timezone.utc) - timedelta(minutes=1)
    to_encode = {"sub": "testuser", "exp": expire}
    token = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

    headers = {"Authorization": f"Bearer {token}"}
    response = client.get("/auth/me", headers=headers)
    assert response.status_code == 401
    assert "Invalid token" in response.json()["detail"]


def test_role_based_access_owner(auth_client: TestClient):
    payload = {
        "name": "RBAC Material",
        "unit": "kg",
        "current_price": 10.5,
        "stock": 100,
    }
    response_owner = auth_client.post("/raw-materials/", json=payload)
    assert response_owner.status_code == 200


def test_role_based_access_worker(worker_client: TestClient):
    payload = {
        "name": "RBAC Material 2",
        "unit": "kg",
        "current_price": 10.5,
        "stock": 100,
    }
    response_worker = worker_client.post("/raw-materials/", json=payload)
    assert response_worker.status_code in [401, 403]
