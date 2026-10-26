"""Suppliers, categories, brands, stock ledger and raw material adjustments."""

import uuid

import pytest


def sku():
    return f"MS-{uuid.uuid4().hex[:8]}"


@pytest.mark.parametrize("path", ["/suppliers/", "/categories/", "/brands/"])
def test_crud_and_duplicate_names(auth_client, path):
    name = f"Name {uuid.uuid4().hex[:6]}"
    body = {"name": name} if path != "/suppliers/" else {"name": name, "phone": "98765"}
    made = auth_client.post(path, json=body)
    assert made.status_code == 201, made.text
    rid = made.json()["id"]
    assert name in [r["name"] for r in auth_client.get(path).json()]

    renamed = auth_client.put(f"{path}{rid}", json={**body, "name": name + " 2"})
    assert renamed.status_code == 200 and renamed.json()["name"] == name + " 2"
    assert auth_client.put(f"{path}{uuid.uuid4()}", json=body).status_code == 404

    if (
        path != "/suppliers/"
    ):  # supplier names are not unique; categories and brands are
        assert auth_client.post(path, json={"name": name + " 2"}).status_code == 409
    assert auth_client.delete(f"{path}{rid}").status_code == 200
    assert auth_client.delete(f"{path}{rid}").status_code == 404


def test_a_category_or_brand_in_use_cannot_be_deleted(auth_client):
    cat = auth_client.post(
        "/categories/", json={"name": f"Cat {uuid.uuid4().hex[:6]}"}
    ).json()
    brand = auth_client.post(
        "/brands/", json={"name": f"Br {uuid.uuid4().hex[:6]}"}
    ).json()
    item = auth_client.post(
        "/items/",
        json={
            "sku": sku(),
            "name": "Chair",
            "selling_price": 100,
            "category_id": cat["id"],
            "brand_id": brand["id"],
        },
    )
    assert item.status_code == 200, item.text
    for path, row in (("/categories/", cat), ("/brands/", brand)):
        r = auth_client.delete(f"{path}{row['id']}")
        assert r.status_code == 409 and "used by products" in r.json()["detail"]


def test_a_supplier_with_purchases_cannot_be_deleted(auth_client):
    sup = auth_client.post("/suppliers/", json={"name": "Teak Traders"}).json()
    mat = auth_client.post(
        "/raw-materials/",
        json={
            "name": f"Teak {uuid.uuid4().hex[:6]}",
            "unit": "cft",
            "current_price": 100,
        },
    ).json()
    p = auth_client.post(
        "/purchases/",
        json={
            "supplier_id": sup["id"],
            "items": [{"raw_material_id": mat["id"], "quantity": 5, "unit_price": 100}],
        },
    )
    assert p.status_code == 201, p.text
    r = auth_client.delete(f"/suppliers/{sup['id']}")
    assert r.status_code == 409 and "past purchases" in r.json()["detail"]


def test_only_owner_manager_can_use_master_data(client, people):
    for role in ("achari", "worker", "sales"):
        h = people[role]["h"]
        assert client.get("/suppliers/", headers=h).status_code == 403
        assert (
            client.post("/categories/", headers=h, json={"name": "x"}).status_code
            == 403
        )
        assert client.get("/inventory/movements", headers=h).status_code == 403
    assert client.get("/suppliers/", headers=people["manager"]["h"]).status_code == 200
    # deleting a supplier is owner-only
    sup = client.post(
        "/suppliers/", headers=people["owner"]["h"], json={"name": "S"}
    ).json()
    assert (
        client.delete(
            f"/suppliers/{sup['id']}", headers=people["manager"]["h"]
        ).status_code
        == 403
    )


def test_stock_adjustments_are_in_the_ledger_with_who_and_why(client, people):
    h = people["manager"]["h"]
    item = client.post(
        "/items/",
        headers=h,
        json={"sku": sku(), "name": "Stool", "selling_price": 50, "current_stock": 10},
    ).json()
    r = client.patch(
        f"/items/{item['id']}/stock",
        headers=h,
        json={"quantity": -3, "notes": "damaged in transport"},
    )
    assert r.status_code == 200 and r.json()["current_stock"] == 7
    assert (
        client.patch(
            f"/items/{item['id']}/stock", headers=h, json={"quantity": -50}
        ).status_code
        == 400
    )

    mat = client.post(
        "/raw-materials/",
        headers=h,
        json={"name": f"Ply {uuid.uuid4().hex[:6]}", "unit": "sheet"},
    ).json()
    a = client.post(
        f"/raw-materials/{mat['id']}/adjust",
        headers=h,
        json={"quantity_change": 12, "notes": "opening count"},
    )
    assert a.status_code == 200 and float(a.json()["stock"]) == 12
    assert (
        client.post(
            f"/raw-materials/{mat['id']}/adjust",
            headers=h,
            json={"quantity_change": -99},
        ).status_code
        == 400
    )
    assert (
        client.post(
            f"/raw-materials/{mat['id']}/adjust", headers=h, json={"quantity_change": 0}
        ).status_code
        == 400
    )

    moves = client.get(
        "/inventory/movements", headers=h, params={"item_id": item["id"]}
    ).json()
    assert len(moves) == 1
    assert moves[0]["name"] == "Stool" and float(moves[0]["quantity"]) == -3
    assert (
        moves[0]["by"].startswith("manager_")
        and "damaged in transport" in moves[0]["notes"]
    )
    mm = client.get(
        "/inventory/movements", headers=h, params={"raw_material_id": mat["id"]}
    ).json()
    assert [float(x["quantity"]) for x in mm] == [12.0] and mm[0]["name"].startswith(
        "Ply"
    )


def test_every_staff_role_can_read_stock_but_only_owner_manager_see_costs(
    client, people
):
    owner = people["owner"]["h"]
    item = client.post(
        "/items/",
        headers=owner,
        json={
            "sku": sku(),
            "name": "Bench",
            "selling_price": 900,
            "purchase_price": 400,
            "production_cost": 350,
        },
    ).json()
    for role in ("owner", "manager"):
        got = client.get(f"/items/{item['id']}", headers=people[role]["h"]).json()
        assert (
            float(got["purchase_price"]) == 400 and float(got["production_cost"]) == 350
        )
    for role in ("sales", "achari", "worker"):
        h = people[role]["h"]
        one = client.get(f"/items/{item['id']}", headers=h)
        assert one.status_code == 200 and float(one.json()["selling_price"]) == 900
        assert (
            float(one.json()["purchase_price"]) == 0
            and float(one.json()["production_cost"]) == 0
        )
        listed = client.get("/items/", headers=h, params={"limit": 100}).json()
        assert listed and all(float(i["purchase_price"]) == 0 for i in listed)
        # reading is all they get
        assert (
            client.put(
                f"/items/{item['id']}", headers=h, json={"name": "x"}
            ).status_code
            == 403
        )
        assert (
            client.patch(
                f"/items/{item['id']}/stock", headers=h, json={"quantity": 1}
            ).status_code
            == 403
        )
    # and the real cost was not overwritten by masking
    assert (
        float(
            client.get(f"/items/{item['id']}", headers=owner).json()["purchase_price"]
        )
        == 400
    )
