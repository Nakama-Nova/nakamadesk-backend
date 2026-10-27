"""
/sync/push and /sync/pull must grant no more than the REST routes do: a worker
cannot rewrite the catalogue through the sync door, and pull only returns what a
role could read through its normal GET routes (no cost prices, wages or sales for
roles that cannot see them).
"""

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from app.models.attendance import Attendance
from app.models.item import Item
from app.models.raw_material import RawMaterial
from app.models.sale import Sale
from app.models.sync import SyncTombstone

# Seeded rows get this timestamp and pulls start a day earlier, so a pull only
# returns what this file created (the shared test DB holds everything else).
FUTURE = datetime(2099, 1, 1, 12, 0, 0)
SINCE = "2098-12-31T12:00:00Z"


def _now():
    return datetime.now(timezone.utc).isoformat()


def _op(entity, action, payload):
    return {
        "id": str(uuid.uuid4()),
        "entity": entity,
        "action": action,
        "payload": payload,
        "updated_at": _now(),
    }


def _push(client, op):
    resp = client.post("/sync/push", json={"operations": [op]})
    assert resp.status_code == 200
    return resp.json()


def _item(db, **kw):
    uid = uuid.uuid4().hex[:8]
    item = Item(
        sku=f"SKU-{uid}",
        name=f"Item {uid}",
        selling_price=Decimal("100.00"),
        current_stock=5,
        gst_percent=Decimal("0"),
        **kw,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


def _create_payloads(db, user_id):
    """One valid create payload per entity."""
    uid = uuid.uuid4().hex[:8]
    item = _item(db)  # a sale needs something to sell
    return {
        "item": {"id": str(uuid.uuid4()), "name": f"N{uid}", "sku": f"S{uid}"},
        "raw_material": {"id": str(uuid.uuid4()), "name": f"Wood {uid}"},
        "attendance": {
            "id": str(uuid.uuid4()),
            "user_id": str(user_id),
            "date": date.today().isoformat(),
            "status": "present",
            "daily_wage": 500.0,
        },
        "sale": {
            "id": str(uuid.uuid4()),
            "items": [{"product_id": str(item.id), "quantity": 1, "price": 100.0}],
            "total_amount": 100.0,
            "payment_method": "cash",
        },
    }


ALLOWED = [
    ("owner", "item"),
    ("owner", "raw_material"),
    ("owner", "attendance"),
    ("owner", "sale"),
    ("manager", "item"),
    ("manager", "raw_material"),
    ("manager", "attendance"),
    ("manager", "sale"),
    ("sales", "attendance"),
    ("sales", "sale"),
]
DENIED = [
    ("sales", "item"),
    ("sales", "raw_material"),
    ("achari", "item"),
    ("achari", "raw_material"),
    ("achari", "attendance"),
    ("achari", "sale"),
    ("worker", "item"),
    ("worker", "raw_material"),
    ("worker", "attendance"),
    ("worker", "sale"),
]


@pytest.mark.parametrize("role,entity", ALLOWED)
def test_push_allowed_for_role(client_as, db, role, entity):
    client, user = client_as(role)
    payload = _create_payloads(db, user.id)[entity]
    data = _push(client, _op(entity, "create", payload))
    assert data["failed"] == [], data
    assert len(data["success"]) == 1


@pytest.mark.parametrize("role,entity", DENIED)
def test_push_denied_for_role_and_nothing_is_written(client_as, db, role, entity):
    client, user = client_as(role)
    payload = _create_payloads(db, user.id)[entity]
    data = _push(client, _op(entity, "create", payload))
    assert data["success"] == []
    assert "access denied" in data["failed"][0]["error"].lower()
    model = {
        "item": Item,
        "raw_material": RawMaterial,
        "attendance": Attendance,
        "sale": Sale,
    }[entity]
    db.expire_all()
    assert db.query(model).filter(model.id == uuid.UUID(payload["id"])).first() is None


def test_worker_cannot_reprice_or_delete_an_item(client_as, db):
    item = _item(db)
    client, _ = client_as("worker")
    upd = _push(
        client, _op("item", "update", {"id": str(item.id), "selling_price": 1.0})
    )
    dele = _push(client, _op("item", "delete", {"id": str(item.id)}))
    assert upd["failed"] and dele["failed"]
    db.expire_all()
    still = db.query(Item).filter(Item.id == item.id).first()
    assert still is not None and still.selling_price == Decimal("100.00")


def test_a_denied_op_does_not_block_the_allowed_ones_in_the_same_batch(client_as, db):
    client, user = client_as("sales")
    p = _create_payloads(db, user.id)
    ops = [_op("item", "create", p["item"]), _op("sale", "create", p["sale"])]
    data = client.post("/sync/push", json={"operations": ops}).json()
    assert [r["client_id"] for r in data["failed"]] == [ops[0]["id"]]
    assert [r["client_id"] for r in data["success"]] == [ops[1]["id"]]


def test_denied_op_can_be_retried_and_is_not_cached_as_failed(client_as, db):
    """Same client_id pushed later by someone allowed must still apply."""
    worker, _ = client_as("worker")
    op = _op("raw_material", "create", {"name": f"Teak {uuid.uuid4().hex[:6]}"})
    assert _push(worker, op)["failed"]
    manager, _ = client_as("manager")
    assert _push(manager, op)["success"]


# ---------------------------------------------------------------------------
# pull
# ---------------------------------------------------------------------------


@pytest.fixture
def seeded(db, client_as):
    """Rows of every kind, stamped FUTURE, plus an item and a material tombstone."""
    owner = client_as("owner")[1]
    other = client_as("worker")[1]
    uid = uuid.uuid4().hex[:8]
    item = _item(db, purchase_price=Decimal("40.00"), production_cost=Decimal("30.00"))
    item.updated_at = FUTURE
    material = RawMaterial(name=f"Sal {uid}", stock=Decimal("5"), updated_at=FUTURE)
    sale = Sale(
        user_id=owner.id,
        total_amount=100.0,
        invoice_number=f"INV-{uid}",
        updated_at=FUTURE,
    )
    att_other = Attendance(
        user_id=other.id,
        date=date.today(),
        status="present",
        daily_wage=Decimal("650.00"),
        recorded_by=owner.id,
        updated_at=FUTURE,
    )
    tomb_item = SyncTombstone(entity="item", record_id=uuid.uuid4(), deleted_at=FUTURE)
    tomb_mat = SyncTombstone(
        entity="raw_material", record_id=uuid.uuid4(), deleted_at=FUTURE
    )
    db.add_all([material, sale, att_other, tomb_item, tomb_mat])
    db.commit()
    return {
        "item": item,
        "material": material,
        "sale": sale,
        "other": other,
        "att_other": att_other,
        "tomb_item": tomb_item,
        "tomb_mat": tomb_mat,
    }


def _pull(client):
    resp = client.get("/sync/pull", params={"last_sync": SINCE})
    assert resp.status_code == 200
    return resp.json()


def _ids(rows, key="id"):
    return {str(r[key]) for r in rows}


def test_pull_for_worker_hides_costs_sales_materials_and_others_wages(
    client_as, db, seeded
):
    client, worker = client_as("worker")
    own = Attendance(
        user_id=worker.id,
        date=date.today(),
        status="present",
        daily_wage=Decimal("600.00"),
        recorded_by=worker.id,
        updated_at=FUTURE,
    )
    db.add(own)
    db.commit()

    body = _pull(client)

    (item,) = [i for i in body["items"] if i["id"] == str(seeded["item"].id)]
    assert Decimal(str(item["purchase_price"])) == 0
    assert Decimal(str(item["production_cost"])) == 0
    assert body["sales"] == []
    assert body["raw_materials"] == []
    assert _ids(body["attendance"]) == {str(own.id)}  # not the other person's wage
    deleted = {d["entity"] for d in body["deleted"]}
    assert "item" in deleted and "raw_material" not in deleted


def test_pull_for_sales_gets_sales_and_attendance_but_no_costs_or_materials(
    client_as, seeded
):
    client, _ = client_as("sales")
    body = _pull(client)
    assert str(seeded["sale"].id) in _ids(body["sales"])
    assert str(seeded["att_other"].id) in _ids(body["attendance"])
    assert body["raw_materials"] == []
    (item,) = [i for i in body["items"] if i["id"] == str(seeded["item"].id)]
    assert Decimal(str(item["purchase_price"])) == 0


@pytest.mark.parametrize("role", ["owner", "manager"])
def test_pull_for_owner_and_manager_is_complete(client_as, seeded, role):
    client, _ = client_as(role)
    body = _pull(client)
    (item,) = [i for i in body["items"] if i["id"] == str(seeded["item"].id)]
    assert Decimal(str(item["purchase_price"])) == Decimal("40.00")
    assert Decimal(str(item["production_cost"])) == Decimal("30.00")
    assert str(seeded["material"].id) in _ids(body["raw_materials"])
    assert str(seeded["sale"].id) in _ids(body["sales"])
    assert str(seeded["att_other"].id) in _ids(body["attendance"])
    deleted = {d["entity"] for d in body["deleted"]}
    assert {"item", "raw_material"} <= deleted


def test_pull_for_achari_matches_worker_visibility(client_as, seeded):
    client, _ = client_as("achari")
    body = _pull(client)
    assert body["sales"] == [] and body["raw_materials"] == []
    assert str(seeded["att_other"].id) not in _ids(body["attendance"])


def test_pull_still_requires_a_login(client):
    assert client.get("/sync/pull", params={"last_sync": SINCE}).status_code == 401
    assert client.post("/sync/push", json={"operations": []}).status_code == 401
