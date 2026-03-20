import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from decimal import Decimal

from app.db.base import Base
from app.db.deps import get_db, get_current_user
from app.main import app
from app.models.user import User
from app.core.config import settings

# Setup testing DB
SQLALCHEMY_DATABASE_URL = settings.DATABASE_URL
engine = create_engine(SQLALCHEMY_DATABASE_URL)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def override_get_db():
    try:
        db = TestingSessionLocal()
        yield db
    finally:
        db.close()

# Mock user with 'owner' role by default
class MockUser:
    def __init__(self, role="owner"):
        self.id = "00000000-0000-0000-0000-000000000001"
        self.username = "test_owner"
        self.role = role

def override_get_current_user():
    return MockUser(role="owner")

app.dependency_overrides[get_db] = override_get_db
app.dependency_overrides[get_current_user] = override_get_current_user

client = TestClient(app)

@pytest.fixture(autouse=True)
def setup_db():
    # Clean up and recreate tables
    try:
        with engine.begin() as conn:
            conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
    except Exception:
        Base.metadata.drop_all(bind=engine)
    
    Base.metadata.create_all(bind=engine)
    yield

def test_manufacturing_flow():
    # 1. Create a Raw Material
    material_payload = {
        "name": "Teak Wood",
        "unit": "CFT",
        "current_price": "1200.00",
        "stock": "50.00"
    }
    response = client.post("/raw-materials/", json=material_payload)
    assert response.status_code == 200
    material_id = response.json()["id"]
    
    # 2. Create an Item
    item_payload = {
        "sku": "CHAIR-001",
        "name": "Luxury Wooden Chair",
        "purchase_price": 500.0,
        "selling_price": 2500.0
    }
    response = client.post("/items/", json=item_payload)
    assert response.status_code == 200
    item_id = response.json()["id"]
    
    # 3. Create a BOM Entry for the Item
    bom_payload = {
        "item_id": item_id,
        "material_id": material_id,
        "required_qty": "2.50",
        "wastage_pct": "10.00"
    }
    response = client.post("/bom/", json=bom_payload)
    assert response.status_code == 200
    
    # 4. Verify Cost Calculation
    # Calculation: 2.50 * 1200.00 * (1 + 10/100) = 3000 * 1.1 = 3300.00
    response = client.get(f"/bom/item/{item_id}")
    assert response.status_code == 200
    data = response.json()
    assert Decimal(str(data["total_cost"])) == Decimal("3300.00")
    
    # 5. Verify Item's production_cost was updated
    response = client.get(f"/items/")
    items = response.json()
    target_item = next(i for i in items if i["id"] == item_id)
    assert Decimal(str(target_item["production_cost"])) == Decimal("3300.00")
    
    # 6. Update Raw Material Price and verify cost impact
    update_payload = {"current_price": "1500.00"}
    response = client.patch(f"/raw-materials/{material_id}", json=update_payload)
    assert response.status_code == 200
    
    # New calculation: 2.50 * 1500.00 * 1.1 = 3750 * 1.1 = 4125.00
    response = client.get(f"/bom/item/{item_id}")
    assert response.status_code == 200
    assert Decimal(str(response.json()["total_cost"])) == Decimal("4125.00")
    
    # Verify Item's production_cost was auto-updated
    response = client.get(f"/items/")
    items = response.json()
    target_item = next(i for i in items if i["id"] == item_id)
    assert Decimal(str(target_item["production_cost"])) == Decimal("4125.00")

def test_rbac_denial():
    # Temporarily override user to 'sales' (who should not have write access)
    def override_sales_user():
        return MockUser(role="sales")
    
    app.dependency_overrides[get_current_user] = override_sales_user
    
    material_payload = {
        "name": "Glue",
        "unit": "LTR",
        "current_price": "200.00",
        "stock": "10.00"
    }
    # Sales cannot create raw material (owner/manager required)
    response = client.post("/raw-materials/", json=material_payload)
    assert response.status_code == 403
    
    # Cleanup override
    app.dependency_overrides[get_current_user] = override_get_current_user
