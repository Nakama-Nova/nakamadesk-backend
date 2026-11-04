from fastapi.testclient import TestClient

from app.models.enums import UserRole
from app.schemas.user import UserCreate
from app.services import auth_service


def make_user(db, username, password="password123", role=UserRole.WORKER):
    """Create a user directly, the way the CLI/owner flow would."""
    return auth_service.register_user(
        db,
        UserCreate(
            username=username,
            email=f"{username}@example.com",
            password=password,
            role=role,
        ),
    )


def test_register_new_user(auth_client: TestClient):
    payload = {
        "username": "newuser",
        "email": "newuser@example.com",
        "password": "testpassword123",
        "full_name": "New User",
        "phone": "1234567890",
    }
    response = auth_client.post("/auth/register", json=payload)
    assert response.status_code == 200
    assert response.json()["username"] == "newuser"


def test_register_duplicate_user(auth_client: TestClient):
    payload = {
        "username": "dupuser",
        "email": "dup@example.com",
        "password": "password123",
    }
    auth_client.post("/auth/register", json=payload)

    # Try to register again with same username
    response = auth_client.post("/auth/register", json=payload)
    assert response.status_code == 400


def test_register_requires_login(client: TestClient):
    payload = {
        "username": "sneaky",
        "email": "sneaky@example.com",
        "password": "password123",
        "role": "owner",
    }
    assert client.post("/auth/register", json=payload).status_code == 401


def test_register_rejects_non_owner(manager_client: TestClient):
    payload = {
        "username": "sneaky2",
        "email": "sneaky2@example.com",
        "password": "password123",
        "role": "owner",
    }
    assert manager_client.post("/auth/register", json=payload).status_code == 403


def test_bootstrap_owner_refused_when_owner_exists(auth_client, db):
    import pytest
    from fastapi import HTTPException

    # auth_client guarantees an owner row exists
    with pytest.raises(HTTPException) as exc:
        auth_service.bootstrap_owner(db, "second", "second@example.com", "pw123456")
    assert exc.value.status_code == 400


def test_login_valid_user(client: TestClient, db):
    make_user(db, "loginuser")

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


def test_access_protected_endpoint(client: TestClient, db):
    make_user(db, "protecteduser")

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
    from datetime import datetime, timedelta, timezone

    import jwt

    from app.core.security import ALGORITHM, SECRET_KEY

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


def test_a_username_works_whatever_its_capitals(client: TestClient, db):
    """The account is 'Swapnac'; she types 'swapnac' (or SWAPNAC): she must get in."""
    make_user(db, "Swapnac", "password123")
    for typed in ("Swapnac", "swapnac", "SWAPNAC", " swapnac "):
        r = client.post(
            "/auth/login", data={"username": typed, "password": "password123"}
        )
        assert r.status_code == 200, (typed, r.text)
        assert (
            r.json()["username"] == "Swapnac"
        )  # the account's own spelling comes back
    assert (
        client.post(
            "/auth/login", data={"username": "swapnac", "password": "wrong-one"}
        ).status_code
        == 401
    )


def test_two_names_that_differ_only_by_case_are_not_allowed(db):
    import pytest
    from fastapi import HTTPException

    make_user(db, "Kavya", "password123")
    with pytest.raises(HTTPException) as e:
        make_user(db, "kavya", "password123")
    assert e.value.status_code == 400 and "already exists" in e.value.detail


def test_an_exact_match_wins_when_old_data_has_two_spellings(client: TestClient, db):
    """Accounts made before this rule may differ only by case: the exact spelling gets its own
    account, and a third spelling matches neither (nobody is guessed)."""
    from app.models.user import User
    from app.services import auth_service

    make_user(db, "Rohan", "password123")
    b = User(
        username="rohan",
        email="rohan2@example.com",
        password_hash=auth_service.hash_password("other-pass-1"),
        role="worker",
        is_active=True,
    )
    db.add(b)
    db.commit()
    assert (
        client.post(
            "/auth/login", data={"username": "Rohan", "password": "password123"}
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/auth/login", data={"username": "rohan", "password": "other-pass-1"}
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/auth/login", data={"username": "ROHAN", "password": "password123"}
        ).status_code
        == 401
    )
