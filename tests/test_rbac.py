from fastapi.testclient import TestClient
from app.models.enums import UserRole


def test_achari_cannot_create_sale(client: TestClient, db):
    # Setup Achari user
    from app.models.user import User
    from app.db.deps import get_current_user
    from app.main import app
    import uuid

    unique_id = uuid.uuid4().hex[:8]
    achari = User(
        username=f"achari_{unique_id}",
        email=f"achari_{unique_id}@example.com",
        password_hash="fakehash",
        role=UserRole.ACHARI,
        is_active=True,
    )
    db.add(achari)
    db.commit()
    db.refresh(achari)

    def override_get_current_user():
        return achari

    app.dependency_overrides[get_current_user] = override_get_current_user

    payload = {
        "items": [{"item_id": str(uuid.uuid4()), "quantity": 1, "unit_price": 100}],
        "total_amount": 100,
        "payment_method": "cash",
    }
    response = client.post("/sales/", json=payload)
    assert response.status_code == 403
    assert "Operation not permitted" in response.json()["detail"]

    app.dependency_overrides.clear()


def test_worker_cannot_access_reports(client: TestClient, db):
    # Setup Worker user
    from app.models.user import User
    from app.db.deps import get_current_user
    from app.main import app
    import uuid

    unique_id = uuid.uuid4().hex[:8]
    worker = User(
        username=f"worker_{unique_id}",
        email=f"worker_{unique_id}@example.com",
        password_hash="fakehash",
        role=UserRole.WORKER,
        is_active=True,
    )
    db.add(worker)
    db.commit()
    db.refresh(worker)

    def override_get_current_user():
        return worker

    app.dependency_overrides[get_current_user] = override_get_current_user

    response = client.get("/reports/sales")
    assert response.status_code == 403

    app.dependency_overrides.clear()


def test_sales_can_create_sale(client: TestClient, db):
    # Setup Sales user
    from app.models.user import User
    from app.db.deps import get_current_user
    from app.main import app
    import uuid

    unique_id = uuid.uuid4().hex[:8]
    sales = User(
        username=f"sales_{unique_id}",
        email=f"sales_{unique_id}@example.com",
        password_hash="fakehash",
        role=UserRole.SALES,
        is_active=True,
    )
    db.add(sales)
    db.commit()
    db.refresh(sales)

    # We also need a real item to avoid 404/validation errors in services
    from app.models.item import Item
    import uuid

    item = Item(
        sku=f"SKU_{uuid.uuid4().hex[:6]}",
        name="Test Item",
        selling_price=100.0,
        current_stock=10,
        is_active=True,
    )
    db.add(item)
    db.commit()
    db.refresh(item)

    def override_get_current_user():
        return sales

    app.dependency_overrides[get_current_user] = override_get_current_user

    payload = {
        "items": [
            {
                "item_id": str(item.id),
                "quantity": 1,
                "unit_price": 100.0,
                "name": "Test Item",  # Some schemas require name
            }
        ],
        "total_amount": 100.0,
        "payment_method": "cash",
    }
    response = client.post("/sales/", json=payload)
    # 400 is fine (e.g. if customer missing), but not 403
    assert response.status_code != 403

    app.dependency_overrides.clear()


def test_owner_can_access_reports(auth_client: TestClient):
    # auth_client is already 'owner' from conftest
    response = auth_client.get("/reports/sales")
    assert response.status_code == 200


def test_invalid_role_rejected(client: TestClient, db):
    # This is tricky because Pydantic might catch it first if we go through /auth/register
    # but let's test the dependency directly with a manually created user with bad role
    from app.models.user import User
    from app.db.deps import get_current_user
    from app.main import app
    import uuid

    unique_id = uuid.uuid4().hex[:8]
    bad_user = User(
        username=f"bad_{unique_id}",
        email=f"bad_{unique_id}@example.com",
        password_hash="fakehash",
        role="invalid_role",  # Manually bypassing enum for test
        is_active=True,
    )
    db.add(bad_user)
    db.commit()
    db.refresh(bad_user)

    def override_get_current_user():
        return bad_user

    app.dependency_overrides[get_current_user] = override_get_current_user

    response = client.get("/reports/sales")
    assert response.status_code == 403

    app.dependency_overrides.clear()
