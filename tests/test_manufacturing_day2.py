import pytest
from fastapi.testclient import TestClient
from decimal import Decimal

def test_multi_material_bom_calculation(auth_client: TestClient):
    """Test a complex BOM with multiple materials and different wastage."""
    # 1. Create 3 materials
    materials = [
        {"name": "Oak Wood", "unit": "CFT", "current_price": "2000.00", "stock": "100"},
        {"name": "Wood Glue", "unit": "LTR", "current_price": "500.00", "stock": "50"},
        {"name": "Steel Nails", "unit": "KG", "current_price": "150.00", "stock": "20"}
    ]
    material_ids = []
    for m in materials:
        resp = auth_client.post("/raw-materials/", json=m)
        material_ids.append(resp.json()["id"])

    # 2. Create an Item
    item_payload = {
        "sku": "TABLE-PRO-001",
        "name": "Professional Oak Table",
        "selling_price": 15000.0
    }
    item_id = auth_client.post("/items/", json=item_payload).json()["id"]

    # 3. Create BOM entries
    # Material 1: 5.0 CFT @ 2000, 5% wastage = 5 * 2000 * 1.05 = 10500
    # Material 2: 0.5 LTR @ 500, 0% wastage = 0.5 * 500 * 1.0 = 250
    # Material 3: 0.2 KG @ 150, 10% wastage = 0.2 * 150 * 1.1 = 33
    # Total expected = 10500 + 250 + 33 = 10783.00
    bom_entries = [
        {"item_id": item_id, "material_id": material_ids[0], "required_qty": "5.00", "wastage_pct": "5.00"},
        {"item_id": item_id, "material_id": material_ids[1], "required_qty": "0.50", "wastage_pct": "0.00"},
        {"item_id": item_id, "material_id": material_ids[2], "required_qty": "0.20", "wastage_pct": "10.00"}
    ]
    for b in bom_entries:
        auth_client.post("/bom/", json=b)

    # 4. Verify Total Cost
    response = auth_client.get(f"/bom/item/{item_id}")
    data = response.json()
    assert Decimal(str(data["total_cost"])) == Decimal("10783.00")
    
    # Verify detailed entries
    assert len(data["entries"]) == 3

def test_fractional_precision_and_rounding(auth_client: TestClient):
    """Test very small quantities and high precision rounding."""
    # Create material with specific price
    material_resp = auth_client.post("/raw-materials/", json={
        "name": "Precision Beads", "unit": "GRAM", "current_price": "12.37", "stock": "1000"
    })
    material_id = material_resp.json()["id"]

    item_id = auth_client.post("/items/", json={
        "sku": "PRECISION-001", "name": "Beaded Ornament"
    }).json()["id"]

    # 0.123 GRAM @ 12.37, 2.5% wastage
    # 0.123 * 12.37 = 1.52151
    # 1.52151 * 1.025 = 1.55954775
    # Rounded to 2 decimal places = 1.56
    response = auth_client.post("/bom/", json={
        "item_id": item_id,
        "material_id": material_id,
        "required_qty": "0.123",
        "wastage_pct": "2.50"
    })
    assert response.status_code == 200

    response = auth_client.get(f"/bom/item/{item_id}")
    assert Decimal(str(response.json()["total_cost"])) == Decimal("1.56")

def test_cascading_price_update_multiple_items(auth_client: TestClient):
    """Ensuring a material price change updates all items using it."""
    # 1. Create a core material
    mat_resp = auth_client.post("/raw-materials/", json={
        "name": "Standard Wood", "unit": "CFT", "current_price": "100.00", "stock": "1000"
    })
    mat_id = mat_resp.json()["id"]

    # 2. Create two items using this material
    items = []
    for i in range(2):
        item_resp = auth_client.post("/items/", json={"sku": f"CORE-{i}", "name": f"Core Item {i}"})
        item_id = item_resp.json()["id"]
        auth_client.post("/bom/", json={
            "item_id": item_id, "material_id": mat_id, "required_qty": "10.00", "wastage_pct": "0.00"
        })
        items.append(item_id)

    # Both should have cost 10 * 100 = 1000.00
    for item_id in items:
        resp = auth_client.get(f"/items/{item_id}")
        assert Decimal(str(resp.json()["production_cost"])) == Decimal("1000.00")

    # 3. Update Material Price to 150.00
    auth_client.patch(f"/raw-materials/{mat_id}", json={"current_price": "150.00"})

    # Both should now have cost 10 * 150 = 1500.00
    for item_id in items:
        resp = auth_client.get(f"/items/{item_id}")
        assert Decimal(str(resp.json()["production_cost"])) == Decimal("1500.00")

def test_bom_entry_deletion_updates_cost(auth_client: TestClient):
    """Verify that removing a BOM entry subtracts its cost from the item."""
    # 1. Add two materials to an item
    m1 = auth_client.post("/raw-materials/", json={"name": "M1", "unit": "X", "current_price": "100", "stock": "10"}).json()["id"]
    m2 = auth_client.post("/raw-materials/", json={"name": "M2", "unit": "X", "current_price": "200", "stock": "10"}).json()["id"]
    item_id = auth_client.post("/items/", json={"sku": "DEL-TEST", "name": "Deletion Test"}).json()["id"]

    b1_id = auth_client.post("/bom/", json={"item_id": item_id, "material_id": m1, "required_qty": "1", "wastage_pct": "0"}).json()["id"]
    auth_client.post("/bom/", json={"item_id": item_id, "material_id": m2, "required_qty": "1", "wastage_pct": "0"}).json()["id"]

    # Initial cost = 100 + 200 = 300
    assert Decimal(str(auth_client.get(f"/items/{item_id}").json()["production_cost"])) == Decimal("300.00")

    # 2. Delete first BOM entry
    auth_client.delete(f"/bom/{b1_id}")

    # New cost = 200
    assert Decimal(str(auth_client.get(f"/items/{item_id}").json()["production_cost"])) == Decimal("200.00")
