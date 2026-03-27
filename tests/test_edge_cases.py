import uuid
from datetime import date

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.user import User


def test_sales_edge_cases(auth_client: TestClient):
    # Setup dependencies
    customer_id = auth_client.post(
        "/customers/", json={"name": "Edge Customer"}
    ).json()["id"]
    item_id = auth_client.post(
        "/items/",
        json={
            "sku": f"SKU-{uuid.uuid4().hex[:4]}",
            "name": "Item",
            "selling_price": 100,
            "current_stock": 5,
        },
    ).json()["id"]

    # Qty 0 -> Reject
    resp = auth_client.post(
        "/sales/",
        json={
            "customer_id": customer_id,
            "items": [{"item_id": item_id, "quantity": 0}],
        },
    )
    assert resp.status_code == 422  # Pydantic gt=0 rejection

    # Qty < 0 -> Reject
    resp2 = auth_client.post(
        "/sales/",
        json={
            "customer_id": customer_id,
            "items": [{"item_id": item_id, "quantity": -5}],
        },
    )
    assert resp2.status_code == 422

    # Stock constraint limits (Request 10 but only 5 in stock)
    resp3 = auth_client.post(
        "/sales/",
        json={
            "customer_id": customer_id,
            "items": [{"item_id": item_id, "quantity": 10}],
        },
    )
    assert resp3.status_code == 400
    assert "Insufficient stock" in resp3.text

    # Duplicate idempotency logic
    client_id = str(uuid.uuid4())
    payload = {
        "customer_id": customer_id,
        "client_id": client_id,
        "items": [{"item_id": item_id, "quantity": 1}],
    }
    pass1 = auth_client.post("/sales/", json=payload)
    assert pass1.status_code == 200
    pass2 = auth_client.post("/sales/", json=payload)
    assert pass2.status_code == 200
    assert (
        pass1.json()["id"] == pass2.json()["id"]
    )  # Safely returned existing record without throwing error or duplicating


def test_workforce_edge_cases(auth_client: TestClient, db: Session):
    user = db.query(User).first()
    uid = str(user.id)
    today_date = date.today().isoformat()

    # Setup initial success
    payload = {
        "user_id": uid,
        "date": today_date,
        "status": "present",
        "daily_wage": 100.0,
    }
    r = auth_client.post("/attendance/", json=payload)
    assert r.status_code == 200

    # Double attendance on same day block
    r_dup = auth_client.post("/attendance/", json=payload)
    assert r_dup.status_code == 400
    assert "already recorded" in r_dup.text

    # Invalid status reject
    bad_status = {
        "user_id": uid,
        "date": "2020-01-01",
        "status": "vacation",
        "daily_wage": 100.0,
    }
    r_bad1 = auth_client.post("/attendance/", json=bad_status)
    assert r_bad1.status_code == 422

    # Negative wage reject
    bad_wage = {
        "user_id": uid,
        "date": "2020-01-02",
        "status": "present",
        "daily_wage": -50.0,
    }
    r_bad2 = auth_client.post("/attendance/", json=bad_wage)
    assert r_bad2.status_code == 422


def test_bom_edge_cases(auth_client: TestClient):
    # Setup item
    item_id = auth_client.post(
        "/items/",
        json={
            "sku": f"BOMITM-{uuid.uuid4().hex[:4]}",
            "name": "BOM Item",
            "selling_price": 100,
            "current_stock": 5,
        },
    ).json()["id"]

    # Missing material -> Caught inherently by UUID validation or Service layer 404
    bad_mat = str(uuid.uuid4())
    r = auth_client.post(
        "/bom/", json={"item_id": item_id, "material_id": bad_mat, "required_qty": 5.0}
    )
    assert r.status_code == 404

    # Zero quantity -> Pydantic rejection via gt=0 constraint
    mat_id = auth_client.post(
        "/raw-materials/", json={"name": "Mat", "unit": "kg"}
    ).json()["id"]
    r2 = auth_client.post(
        "/bom/", json={"item_id": item_id, "material_id": mat_id, "required_qty": 0}
    )
    assert r2.status_code == 422
