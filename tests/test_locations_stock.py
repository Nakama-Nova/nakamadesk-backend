"""
Locations and stock by location: the Shop and plant units as places, per-location
levels that split the existing totals, transfers between places, and sales and
purchases that record where they happened.
"""

import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.models.inventory_movement import InventoryMovement
from app.models.item import Item
from app.models.plant_unit import PlantUnit
from app.models.raw_material import RawMaterial
from app.models.sale import Sale
from app.models.stock_level import StockLevel


@pytest.fixture(autouse=True)
def only_my_locations(db):
    """The shared test DB keeps every location ever made: switch them off, and mine after."""
    db.query(PlantUnit).update({PlantUnit.is_active: False})
    db.commit()
    yield
    db.query(PlantUnit).update({PlantUnit.is_active: False})
    db.commit()


def make_location(db, name, kind="plant", active=True):
    uid = uuid.uuid4().hex[:8]
    unit = PlantUnit(
        name=f"{name} {uid}",
        code=f"{name[:2]}{uid[:6]}".upper(),  # at most 10 characters
        latitude=12.9,
        longitude=77.5,
        radius_m=50,
        kind=kind,
        has_till=(kind == "shop"),
        is_active=active,
    )
    db.add(unit)
    db.commit()
    db.refresh(unit)
    return unit


def make_item(db, stock=10):
    uid = uuid.uuid4().hex[:8]
    item = Item(
        sku=f"LOC-{uid}",
        name=f"Chair {uid}",
        selling_price=Decimal("100.00"),
        gst_percent=Decimal("0"),
        current_stock=stock,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


def make_material(db, stock="20"):
    uid = uuid.uuid4().hex[:8]
    mat = RawMaterial(
        name=f"Teak {uid}",
        unit="cft",
        current_price=Decimal("100.00"),
        stock=Decimal(stock),
    )
    db.add(mat)
    db.commit()
    db.refresh(mat)
    return mat


def move(client, frm, to, qty, **what):
    body = {
        "from_location_id": str(frm.id) if frm else None,
        "to_location_id": str(to.id) if to else None,
        "quantity": qty,
        **what,
    }
    return client.post("/stock/transfers", json=body)


def level(db, loc, item=None, mat=None):
    db.expire_all()
    q = db.query(StockLevel).filter(StockLevel.location_id == loc.id)
    q = q.filter(
        StockLevel.item_id == item.id if item else StockLevel.raw_material_id == mat.id
    )
    row = q.first()
    return Decimal(str(row.quantity)) if row else Decimal("0")


def total(db, item=None, mat=None):
    db.expire_all()
    if item:
        return db.get(Item, item.id).current_stock
    return db.get(RawMaterial, mat.id).stock


# ------------------------------------------------------------------ the places
def test_owner_makes_a_shop_and_an_update_without_kind_keeps_it(client_as, db):
    owner, _ = client_as("owner")
    body = {
        "name": f"Open Shop {uuid.uuid4().hex[:6]}",
        "code": "shp" + uuid.uuid4().hex[:3],
        "latitude": 12.9,
        "longitude": 77.5,
        "radius_m": 40,
        "kind": "shop",
        "has_till": True,
    }
    made = owner.post("/plant-units/", json=body)
    assert made.status_code == 201, made.text
    assert made.json()["kind"] == "shop" and made.json()["has_till"] is True

    # an older client that does not send kind or has_till must not reset them
    older = {k: v for k, v in body.items() if k not in ("kind", "has_till")}
    older["radius_m"] = 60
    again = owner.put(f"/plant-units/{made.json()['id']}", json=older)
    assert again.status_code == 200, again.text
    assert again.json()["kind"] == "shop" and again.json()["has_till"] is True
    assert again.json()["radius_m"] == 60


def test_a_unit_is_a_plant_by_default_and_other_kinds_are_refused(client_as):
    owner, _ = client_as("owner")
    body = {
        "name": f"Painting {uuid.uuid4().hex[:6]}",
        "code": "pnt" + uuid.uuid4().hex[:3],
        "latitude": 12.9,
        "longitude": 77.5,
        "radius_m": 40,
    }
    made = owner.post("/plant-units/", json=body)
    assert made.json()["kind"] == "plant" and made.json()["has_till"] is False
    bad = owner.post(
        "/plant-units/",
        json={
            **body,
            "name": "x" + body["name"],
            "code": "x1" + uuid.uuid4().hex[:3],
            "kind": "warehouse",
        },
    )
    assert bad.status_code == 422


# ----------------------------------------------------------------- transfers
def test_placing_unassigned_stock_then_moving_it_on(client_as, db):
    mfg = make_location(db, "Manufacturing")
    shop = make_location(db, "Shop", kind="shop")
    item = make_item(db, stock=10)
    manager, user = client_as("manager")
    what = {"item_id": str(item.id)}

    placed = move(manager, None, mfg, 4, **what)
    assert placed.status_code == 201, placed.text
    assert (
        placed.json()["to_quantity"] == "4.0000"
        or Decimal(placed.json()["to_quantity"]) == 4
    )
    assert level(db, mfg, item=item) == 4
    assert total(db, item=item) == 10  # the total never moves

    unassigned = {
        r["item_id"]: Decimal(r["quantity"])
        for r in manager.get("/stock/unassigned?limit=500").json()
        if r["item_id"]
    }
    assert unassigned[str(item.id)] == 6

    moved = move(manager, mfg, shop, 3, **what)
    assert moved.status_code == 201, moved.text
    assert (level(db, mfg, item=item), level(db, shop, item=item)) == (1, 3)
    assert total(db, item=item) == 10

    rows = (
        db.query(InventoryMovement)
        .filter(InventoryMovement.transfer_id == uuid.UUID(moved.json()["transfer_id"]))
        .all()
    )
    assert sorted((r.movement_type, r.location_id, r.quantity) for r in rows) == sorted(
        [("transfer_out", mfg.id, -3), ("transfer_in", shop.id, 3)]
    )
    assert all(r.created_by == user.id for r in rows)

    listed = manager.get("/stock/transfers").json()
    mine = [t for t in listed if t["transfer_id"] == moved.json()["transfer_id"]]
    assert len(mine) == 1
    assert mine[0]["from_location_id"] == str(mfg.id)
    assert mine[0]["to_location_id"] == str(shop.id)
    assert Decimal(mine[0]["quantity"]) == 3


def test_a_transfer_cannot_take_more_than_is_there(client_as, db):
    mfg = make_location(db, "Manufacturing")
    shop = make_location(db, "Shop", kind="shop")
    item = make_item(db, stock=10)
    owner, _ = client_as("owner")
    what = {"item_id": str(item.id)}
    assert move(owner, None, mfg, 4, **what).status_code == 201

    over = move(owner, mfg, shop, 5, **what)
    assert over.status_code == 400 and "Only 4" in over.json()["detail"]
    over_unassigned = move(owner, None, shop, 7, **what)  # only 6 unassigned
    assert over_unassigned.status_code == 400
    assert "unassigned" in over_unassigned.json()["detail"]
    assert level(db, mfg, item=item) == 4 and level(db, shop, item=item) == 0
    assert (
        db.query(InventoryMovement)
        .filter(
            InventoryMovement.item_id == item.id,
            InventoryMovement.movement_type == "transfer_out",
        )
        .count()
        == 1
    )  # nothing written by the refused ones


def test_raw_material_moves_in_fractions_but_items_do_not(client_as, db):
    mfg = make_location(db, "Manufacturing")
    mac = make_location(db, "Machinery")
    mat = make_material(db, stock="20")
    item = make_item(db, stock=10)
    owner, _ = client_as("owner")

    assert move(owner, None, mfg, "2.5", raw_material_id=str(mat.id)).status_code == 201
    assert move(owner, mfg, mac, "1.25", raw_material_id=str(mat.id)).status_code == 201
    assert level(db, mfg, mat=mat) == Decimal("1.25")
    assert level(db, mac, mat=mat) == Decimal("1.25")

    r = move(owner, None, mfg, "1.5", item_id=str(item.id))
    assert r.status_code == 400 and "whole" in r.json()["detail"]


@pytest.mark.parametrize(
    "mutate",
    [
        {"quantity": 0},
        {"quantity": -2},
        {"item_id": None, "raw_material_id": None},
        {"from_location_id": None, "to_location_id": None},
    ],
)
def test_nonsense_transfers_are_refused(client_as, db, mutate):
    mfg = make_location(db, "Manufacturing")
    item = make_item(db)
    owner, _ = client_as("owner")
    body = {
        "item_id": str(item.id),
        "from_location_id": None,
        "to_location_id": str(mfg.id),
        "quantity": 1,
        **mutate,
    }
    assert owner.post("/stock/transfers", json=body).status_code == 422


def test_same_place_unknown_place_and_unknown_item_are_refused(client_as, db):
    mfg = make_location(db, "Manufacturing")
    off = make_location(db, "Old", active=False)
    item = make_item(db)
    owner, _ = client_as("owner")
    what = {"item_id": str(item.id)}
    assert move(owner, mfg, mfg, 1, **what).status_code == 400
    assert move(owner, None, off, 1, **what).status_code == 400  # switched off
    ghost = {
        "item_id": str(item.id),
        "from_location_id": None,
        "to_location_id": str(uuid.uuid4()),
        "quantity": 1,
    }
    assert owner.post("/stock/transfers", json=ghost).status_code == 400
    assert move(owner, None, mfg, 1, item_id=str(uuid.uuid4())).status_code == 404


@pytest.mark.parametrize(
    "role,can_move,can_view",
    [
        ("owner", True, True),
        ("manager", True, True),
        ("sales", False, True),
        ("achari", False, False),
        ("worker", False, False),
        ("artisan", False, False),
    ],
)
def test_who_can_move_and_who_can_look(client_as, db, role, can_move, can_view):
    mfg = make_location(db, "Manufacturing")
    item = make_item(db)
    client, _ = client_as(role)
    moved = move(client, None, mfg, 1, item_id=str(item.id))
    assert (moved.status_code == 201) is can_move
    if not can_move:
        assert moved.status_code == 403
    for path in ("/stock/levels", "/stock/unassigned", "/stock/transfers"):
        assert (client.get(path).status_code == 200) is can_view


def test_levels_can_be_filtered_by_location(client_as, db):
    mfg = make_location(db, "Manufacturing")
    shop = make_location(db, "Shop", kind="shop")
    item = make_item(db)
    owner, _ = client_as("owner")
    move(owner, None, mfg, 2, item_id=str(item.id))
    move(owner, None, shop, 3, item_id=str(item.id))
    rows = owner.get(f"/stock/levels?location_id={shop.id}").json()
    assert [(r["location_name"], Decimal(r["quantity"])) for r in rows] == [
        (shop.name, 3)
    ]
    both = owner.get(f"/stock/levels?item_id={item.id}").json()
    assert len(both) == 2 and all(r["name"] == item.name for r in both)


# --------------------------------------------------------------------- sales
def sell(client, item, qty=1, **extra):
    return client.post(
        "/sales/", json={"items": [{"item_id": str(item.id), "quantity": qty}], **extra}
    )


def test_a_sale_is_recorded_at_the_one_shop(client_as, db):
    shop = make_location(db, "Shop", kind="shop")
    mfg = make_location(db, "Manufacturing")
    item = make_item(db, stock=5)
    owner, _ = client_as("owner")
    assert move(owner, None, shop, 5, item_id=str(item.id)).status_code == 201

    r = sell(owner, item, 2)
    assert r.status_code == 200, r.text
    assert r.json()["location_id"] == str(shop.id)
    assert total(db, item=item) == 3
    assert level(db, shop, item=item) == 3
    assert level(db, mfg, item=item) == 0
    sale = db.get(Sale, uuid.UUID(r.json()["id"]))
    assert sale.location_id == shop.id
    out = (
        db.query(InventoryMovement)
        .filter(InventoryMovement.reference_id == sale.id)
        .one()
    )
    assert out.location_id == shop.id and out.quantity == -2


def test_a_sale_before_any_stock_was_placed_can_leave_the_shop_negative(client_as, db):
    shop = make_location(db, "Shop", kind="shop")
    item = make_item(db, stock=5)  # all unassigned
    owner, _ = client_as("owner")
    assert sell(owner, item, 2).status_code == 200  # the counter never stops
    assert total(db, item=item) == 3
    assert level(db, shop, item=item) == -2  # sold before it was transferred here
    # the 5 that were never placed are still unassigned; moving them to the shop
    # puts its level right: -2 + 5 = 3, which is what is on the shelf
    assert move(owner, None, shop, 5, item_id=str(item.id)).status_code == 201
    assert level(db, shop, item=item) == 3


def test_a_sale_names_its_own_location_or_is_refused(client_as, db):
    make_location(db, "Shop", kind="shop")
    mfg = make_location(db, "Manufacturing")
    item = make_item(db, stock=5)
    owner, _ = client_as("owner")
    r = sell(owner, item, 1, location_id=str(mfg.id))
    assert r.status_code == 200 and r.json()["location_id"] == str(mfg.id)
    assert level(db, mfg, item=item) == -1
    bad = sell(owner, item, 1, location_id=str(uuid.uuid4()))
    assert bad.status_code == 400
    assert total(db, item=item) == 4  # the refused sale changed nothing


def test_without_exactly_one_shop_a_sale_is_not_placed(client_as, db):
    item = make_item(db, stock=5)
    owner, _ = client_as("owner")

    none = sell(owner, item, 1)  # no shop at all: exactly as before locations
    assert none.status_code == 200 and none.json()["location_id"] is None
    assert db.query(StockLevel).filter(StockLevel.item_id == item.id).count() == 0

    make_location(db, "Shop A", kind="shop")
    make_location(db, "Shop B", kind="shop")
    two = sell(owner, item, 1)  # which shop? the client must say
    assert two.status_code == 200 and two.json()["location_id"] is None


def test_an_offline_counter_sale_over_sync_lands_at_the_shop(client_as, db):
    shop = make_location(db, "Shop", kind="shop")
    item = make_item(db, stock=5)
    client, _ = client_as("sales")
    sale_id = str(uuid.uuid4())
    op = {
        "id": str(uuid.uuid4()),
        "entity": "sale",
        "action": "create",
        "payload": {
            "id": sale_id,
            "items": [{"product_id": str(item.id), "quantity": 1, "price": 100}],
            "total_amount": 100,
            "payment_method": "cash",
        },
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    out = client.post("/sync/push", json={"operations": [op]}).json()
    assert out["failed"] == [], out
    db.expire_all()
    assert db.get(Sale, uuid.UUID(sale_id)).location_id == shop.id
    assert level(db, shop, item=item) == -1


# ----------------------------------------------------------- receipts, adjusts
def test_a_purchase_can_say_where_the_goods_arrived(client_as, db):
    mfg = make_location(db, "Manufacturing")
    mat = make_material(db, stock="0")
    owner, _ = client_as("owner")
    sup = owner.post("/suppliers/", json={"name": "Timber Depot"}).json()

    def buy(qty):
        made = owner.post(
            "/purchases/",
            json={
                "supplier_id": sup["id"],
                "items": [
                    {"raw_material_id": str(mat.id), "quantity": qty, "unit_price": 100}
                ],
            },
        )
        assert made.status_code == 201, made.text
        return made.json()["id"]

    first = owner.patch(f"/purchases/{buy(5)}/confirm?location_id={mfg.id}")
    assert first.status_code == 200, first.text
    assert level(db, mfg, mat=mat) == 5 and total(db, mat=mat) == 5

    second = owner.patch(f"/purchases/{buy(3)}/confirm")  # no location: unassigned
    assert second.status_code == 200
    assert level(db, mfg, mat=mat) == 5 and total(db, mat=mat) == 8

    third_id = buy(1)
    bad = owner.patch(f"/purchases/{third_id}/confirm?location_id={uuid.uuid4()}")
    assert bad.status_code == 400
    assert total(db, mat=mat) == 8  # still pending, nothing moved


def test_manual_adjustments_can_name_a_location(client_as, db):
    mfg = make_location(db, "Manufacturing")
    item = make_item(db, stock=5)
    mat = make_material(db, stock="10")
    owner, _ = client_as("owner")

    r = owner.patch(
        f"/items/{item.id}/stock", json={"quantity": 3, "location_id": str(mfg.id)}
    )
    assert r.status_code == 200, r.text
    assert level(db, mfg, item=item) == 3 and total(db, item=item) == 8

    r = owner.post(
        f"/raw-materials/{mat.id}/adjust",
        json={"quantity_change": "-2.5", "location_id": str(mfg.id)},
    )
    assert r.status_code == 200, r.text
    assert level(db, mfg, mat=mat) == Decimal("-2.5") and total(db, mat=mat) == Decimal(
        "7.5"
    )

    ghost = owner.patch(
        f"/items/{item.id}/stock",
        json={"quantity": 1, "location_id": str(uuid.uuid4())},
    )
    assert ghost.status_code == 400 and total(db, item=item) == 8

    plain = owner.patch(f"/items/{item.id}/stock", json={"quantity": 1})  # as before
    assert plain.status_code == 200 and total(db, item=item) == 9


def test_the_movement_list_shows_where_it_happened(client_as, db):
    mfg = make_location(db, "Manufacturing")
    item = make_item(db)
    owner, _ = client_as("owner")
    move(owner, None, mfg, 2, item_id=str(item.id))
    rows = owner.get(f"/inventory/movements?item_id={item.id}").json()
    assert {(r["movement_type"], r["location_id"]) for r in rows} == {
        ("transfer_out", None),
        ("transfer_in", str(mfg.id)),
    }


def test_an_offline_sale_keeps_the_place_it_was_sold_from(auth_client, db):
    """A bill saved on the desktop while offline and pushed later is sold from the place it
    named, not from the default shop."""
    from datetime import datetime, timezone

    shop = make_location(db, "Shop", kind="shop")
    stall = make_location(db, "Stall", kind="plant")
    item = make_item(db, stock=10)
    op = {
        "id": str(uuid.uuid4()),
        "entity": "sale",
        "action": "create",
        "payload": {
            "customer_name": "Walk-in",
            "customer_phone": "9000000042",
            "items": [{"product_id": str(item.id), "quantity": 2, "price": 100}],
            "total_amount": 200,
            "payment_method": "cash",
            "location_id": str(stall.id),
        },
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    resp = auth_client.post("/sync/push", json={"operations": [op]})
    assert resp.status_code == 200 and not resp.json()["failed"], resp.text
    sale_id = resp.json()["success"][0]["record_id"]
    assert auth_client.get(f"/sales/{sale_id}").json()["location_id"] == str(stall.id)
    assert str(shop.id) != str(stall.id)
