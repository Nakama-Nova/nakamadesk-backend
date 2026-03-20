from fastapi.testclient import TestClient
from datetime import date
from uuid import uuid4
from app.models.user import User


def test_full_business_flow(auth_client: TestClient):
    """
    Scenario 1: Item -> RM -> BOM -> Sale -> Validation (Stock, P&L, Dashboard)
    """
    # 1. Create Raw Material
    rm_id = auth_client.post(
        "/raw-materials/",
        json={
            "name": "E2E Wood",
            "unit": "CFT",
            "current_price": "100.0",
            "stock": "100.0",
        },
    ).json()["id"]

    # 2. Create Item
    item_id = auth_client.post(
        "/items/",
        json={
            "sku": "E2E-CHAIR",
            "name": "E2E Chair",
            "selling_price": 500.0,
            "current_stock": 10,
        },
    ).json()["id"]

    # 3. Create BOM (1 CFT wood @ $100 -> Cost = $100)
    auth_client.post(
        "/bom/",
        json={
            "item_id": item_id,
            "material_id": rm_id,
            "required_qty": "1.0",
            "wastage_pct": "0.0",
        },
    )

    # Validate cost is 100
    assert (
        float(auth_client.get(f"/items/{item_id}").json()["production_cost"]) == 100.0
    )

    # 4. Create Sale (2 qty -> Revenue 1000)
    auth_client.post("/sales/", json={"items": [{"item_id": item_id, "quantity": 2}]})

    # 5. Check stock reduced
    assert auth_client.get(f"/items/{item_id}").json()["current_stock"] == 8

    # 6. Check Profit/Loss (Cost for 2 is 200, Revenue 1000 => Profit 800)
    # Note: PL report looks at all data, so we check that it reflects the delta correctly.
    pl_data = auth_client.get("/reports/profit-loss").json()
    assert pl_data is not None
    assert float(pl_data["total_revenue"]) >= 1000.0
    assert float(pl_data["total_cost"]) >= 200.0

    # 7. Check Dashboard updated
    dash_data = auth_client.get("/dashboard/summary").json()
    assert dash_data["today_sales_count"] >= 1
    assert float(dash_data["today_revenue"]) >= 1000.0


def test_workforce_and_sales_flow(auth_client: TestClient, db):
    """
    Scenario 2: Mark attendance -> create sale -> check dashboard reflects both
    """
    # 1. Setup a worker
    worker_id = str(uuid4().hex[:8])
    worker = User(
        username=f"e2e_worker_{worker_id}",
        email=f"e2e_{worker_id}@ex.com",
        password_hash="fakehash",
        role="worker",
    )
    db.add(worker)
    db.commit()
    db.refresh(worker)

    # 2. Mark attendance (Wage: 500.0)
    auth_client.post(
        "/attendance/",
        json={
            "user_id": str(worker.id),
            "date": str(date.today()),
            "status": "present",
            "daily_wage": 500.0,
        },
    )

    # 3. Create an item and a sale
    item_id = auth_client.post(
        "/items/",
        json={
            "sku": f"WF-SALE-{worker_id}",
            "name": "WF Item",
            "selling_price": 200.0,
            "current_stock": 5,
        },
    ).json()["id"]

    auth_client.post("/sales/", json={"items": [{"item_id": item_id, "quantity": 1}]})

    # 4. Check wages updated
    wages = auth_client.get(f"/wages/pending?user_id={worker.id}").json()
    assert len(wages) == 1
    assert float(wages[0]["total_amount"]) == 500.0

    # 5. Check dashboard reflects both missing wage and sales
    dash_data = auth_client.get("/dashboard/summary").json()
    assert float(dash_data["today_revenue"]) >= 200.0
    assert float(dash_data["pending_wages_total"]) >= 500.0
