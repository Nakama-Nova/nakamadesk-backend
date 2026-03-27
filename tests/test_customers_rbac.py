import uuid

from fastapi.testclient import TestClient

from app.db.deps import get_current_user
from app.main import app
from app.models.enums import UserRole
from app.models.user import User


def create_test_user(db, role: UserRole):
    unique_id = uuid.uuid4().hex[:8]
    user = User(
        username=f"test_{role}_{unique_id}",
        email=f"test_{role}_{unique_id}@example.com",
        password_hash="fakehash",
        role=role,
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def setup_user_override(user):
    def override_get_current_user():
        return user

    app.dependency_overrides[get_current_user] = override_get_current_user


def teardown_override():
    app.dependency_overrides.clear()


def test_worker_cannot_access_customers(client: TestClient, db):
    worker = create_test_user(db, UserRole.WORKER)
    setup_user_override(worker)

    response = client.get("/customers/")
    assert response.status_code == 403

    teardown_override()


def test_achari_cannot_access_customers(client: TestClient, db):
    achari = create_test_user(db, UserRole.ACHARI)
    setup_user_override(achari)

    response = client.get("/customers/")
    assert response.status_code == 403

    teardown_override()


def test_sales_can_access_customers(client: TestClient, db):
    sales = create_test_user(db, UserRole.SALES)
    setup_user_override(sales)

    # List
    response = client.get("/customers/")
    assert response.status_code == 200

    # Create
    payload = {"name": "Test Customer", "phone": "1234567890"}
    response = client.post("/customers/", json=payload)
    assert response.status_code == 200

    teardown_override()


def test_sales_cannot_delete_customer(client: TestClient, db):
    sales = create_test_user(db, UserRole.SALES)
    setup_user_override(sales)

    customer_id = str(uuid.uuid4())
    response = client.delete(f"/customers/{customer_id}")
    assert response.status_code == 403  # Rejected by check_role

    teardown_override()


def test_manager_can_delete_customer(client: TestClient, db):
    manager = create_test_user(db, UserRole.MANAGER)
    setup_user_override(manager)

    # First create a customer to delete
    from app.models.customer import Customer

    customer = Customer(id=uuid.uuid4(), name="To Delete", phone="0000000000")
    db.add(customer)
    db.commit()

    response = client.delete(f"/customers/{customer.id}")
    assert response.status_code == 200

    teardown_override()
