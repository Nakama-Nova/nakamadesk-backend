"""
The server records every pushed change under the token that pushes it. When the client
says who made a change (`user_id`), a push under a different user's token is refused
with its own status so the client can hold the change for the right person.
"""

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from app.models.item import Item
from app.models.sale import Sale


def _item(db):
    uid = uuid.uuid4().hex[:8]
    item = Item(
        sku=f"SKU-{uid}",
        name=f"Item {uid}",
        selling_price=Decimal("100.00"),
        current_stock=5,
        gst_percent=Decimal("0"),
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


def _sale_op(item, user_id=None, op_id=None):
    op = {
        "id": op_id or str(uuid.uuid4()),
        "entity": "sale",
        "action": "create",
        "payload": {
            "id": str(uuid.uuid4()),
            "items": [{"product_id": str(item.id), "quantity": 1, "price": 100.0}],
            "total_amount": 100.0,
            "payment_method": "cash",
        },
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    if user_id is not None:
        op["user_id"] = str(user_id)
    return op


def _push(client, op):
    resp = client.post("/sync/push", json={"operations": [op]})
    assert resp.status_code == 200
    return resp.json()


def test_a_change_made_by_the_pusher_is_recorded_under_them(client_as, db):
    client, user = client_as("sales")
    op = _sale_op(_item(db), user_id=user.id)
    data = _push(client, op)
    assert data["failed"] == [] and len(data["success"]) == 1
    sale = db.query(Sale).filter(Sale.id == uuid.UUID(op["payload"]["id"])).one()
    assert sale.user_id == user.id


def test_a_change_made_by_someone_else_is_refused_and_not_written(client_as, db):
    _, maker = client_as("sales")  # the sales user who rang up the bill offline
    client, _ = client_as("manager")  # a different person is signed in at push time
    op = _sale_op(_item(db), user_id=maker.id)

    data = _push(client, op)

    assert data["success"] == []
    (refused,) = data["failed"]
    assert refused["client_id"] == op["id"] and refused["status"] == "wrong_user"
    assert "different user" in refused["error"].lower()
    db.expire_all()
    assert (
        db.query(Sale).filter(Sale.id == uuid.UUID(op["payload"]["id"])).first() is None
    )


def test_a_refused_change_is_not_remembered_so_the_right_user_can_send_it_later(
    client_as, db
):
    _, maker = client_as("sales")
    other, _ = client_as("manager")
    op = _sale_op(_item(db), user_id=maker.id)
    assert _push(other, op)["failed"][0]["status"] == "wrong_user"

    maker_client, _ = client_as("sales")  # a fresh sales user: still not the maker
    assert _push(maker_client, op)["failed"][0]["status"] == "wrong_user"

    # the maker themself, signed in again
    from app.db.deps import get_current_user
    from app.main import app
    from app.models.user import User

    me = db.query(User).filter(User.id == maker.id).one()
    app.dependency_overrides[get_current_user] = lambda: me
    data = _push(maker_client, op)
    assert data["failed"] == [] and len(data["success"]) == 1


def test_changes_without_a_maker_still_work_for_older_clients(client_as, db):
    client, user = client_as("sales")
    data = _push(client, _sale_op(_item(db)))
    assert data["failed"] == [] and len(data["success"]) == 1


def test_one_wrong_user_op_does_not_block_the_others_in_the_batch(client_as, db):
    _, maker = client_as("sales")
    client, me = client_as("manager")
    mine = _sale_op(_item(db), user_id=me.id)
    theirs = _sale_op(_item(db), user_id=maker.id)
    data = client.post("/sync/push", json={"operations": [theirs, mine]}).json()
    assert [r["client_id"] for r in data["failed"]] == [theirs["id"]]
    assert [r["client_id"] for r in data["success"]] == [mine["id"]]
