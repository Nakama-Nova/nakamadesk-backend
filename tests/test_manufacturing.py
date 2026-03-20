import pytest
from fastapi.testclient import TestClient
from decimal import Decimal

def test_manufacturing_flow(auth_client: TestClient):
    # 1. Create a Raw Material
    material_payload = {
        "name": "Teak Wood",
        "unit": "CFT",
        "current_price": "1200.00",
        "stock": "50.00"
    }
    response = auth_client.post("/raw-materials/", json=material_payload)
    assert response.status_code == 200
    material_id = response.json()["id"]
    
    # 2. Create an Item
    item_payload = {
        "sku": "CHAIR-001",
        "name": "Luxury Wooden Chair",
        "purchase_price": 500.0,
        "selling_price": 2500.0
    }
    response = auth_client.post("/items/", json=item_payload)
    assert response.status_code == 200
    item_id = response.json()["id"]
    
    # 3. Create a BOM Entry for the Item
    bom_payload = {
        "item_id": item_id,
        "material_id": material_id,
        "required_qty": "2.50",
        "wastage_pct": "10.00"
    }
    response = auth_client.post("/bom/", json=bom_payload)
    assert response.status_code == 200
    
    # 4. Verify Cost Calculation
    # Calculation: 2.50 * 1200.00 * (1 + 10/100) = 3000 * 1.1 = 3300.00
    response = auth_client.get(f"/bom/item/{item_id}")
    assert response.status_code == 200
    data = response.json()
    assert Decimal(str(data["total_cost"])) == Decimal("3300.00")
    
    # 5. Verify Item's production_cost was updated
    response = auth_client.get(f"/items/")
    items = response.json()
    target_item = next(i for i in items if i["id"] == item_id)
    assert Decimal(str(target_item["production_cost"])) == Decimal("3300.00")
    
    # 6. Update Raw Material Price and verify cost impact
    update_payload = {"current_price": "1500.00"}
    response = auth_client.patch(f"/raw-materials/{material_id}", json=update_payload)
    assert response.status_code == 200
    
    # New calculation: 2.50 * 1500.00 * 1.1 = 3750 * 1.1 = 4125.00
    response = auth_client.get(f"/bom/item/{item_id}")
    assert response.status_code == 200
    assert Decimal(str(response.json()["total_cost"])) == Decimal("4125.00")
    
    # Verify Item's production_cost was auto-updated
    response = auth_client.get(f"/items/")
    items = response.json()
    target_item = next(i for i in items if i["id"] == item_id)
    assert Decimal(str(target_item["production_cost"])) == Decimal("4125.00")

def test_rbac_denial(client: TestClient, db):
    # Temporarily override user to 'sales' (who should not have write access)
    from app.models.user import User
    from app.db.deps import get_current_user
    from app.main import app
    
    sales_user = User(username="sales_user", email="sales@ex.com", password_hash="hash", role="sales")
    db.add(sales_user)
    db.commit()
    
    def override_sales_user():
        return sales_user
    
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
    
    # No need to manually clear overrides as the client fixture handles it
