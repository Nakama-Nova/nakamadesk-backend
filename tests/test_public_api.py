"""Public website API: catalogue, checkout, enquiry, tracking, uploads, limits."""

import io
import uuid

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.core import ratelimit
from app.core.config import settings
from app.db.deps import get_current_user
from app.main import app
from app.models.category import Category
from app.services import media_service

PHONE = "9876500011"


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    ratelimit.reset()
    monkeypatch.setattr(settings, "MEDIA_DIR", str(tmp_path / "media"))
    monkeypatch.setattr(settings, "DELIVERY_PINCODES", [])
    monkeypatch.setattr(settings, "PUBLIC_RATE_LIMIT", 100)
    yield
    ratelimit.reset()


def make_item(client, **over):
    body = {
        "sku": f"PUB-{uuid.uuid4().hex[:8]}",
        "name": "Carved Teak Armchair",
        "selling_price": 18500,
        "purchase_price": 9000,
        "production_cost": 7000,
        "current_stock": 4,
        "is_published": True,
        "short_desc": "Hand-carved backrest",
        "wood_type": "Teak",
        "dimensions_mm": {"l": 700, "w": 650, "h": 950},
        **over,
    }
    r = client.post("/items/", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def checkout_body(slug, qty=1, **over):
    return {
        "name": "Meena",
        "phone": PHONE,
        "address": "12 Temple Road",
        "town": "Puttur",
        "pincode": "574201",
        "items": [{"slug": slug, "quantity": qty}],
        **over,
    }


def png_bytes(color="red"):
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), color).save(buf, "PNG")
    return buf.getvalue()


GLB = b"glTF" + b"\x02\x00\x00\x00" + b"\x00" * 16


# ------------------------------------------------------------------ catalogue
def test_catalog_shows_only_published_and_never_costs(auth_client: TestClient):
    shown = make_item(auth_client, name="Shown Stool")
    make_item(auth_client, name="Hidden Stool", is_published=False)
    make_item(auth_client, name="Gone Stool", is_active=False)

    rows = auth_client.get("/public/catalog", params={"q": "Stool"}).json()
    assert [r["name"] for r in rows] == ["Shown Stool"]
    row = rows[0]
    assert row["slug"] == shown["slug"]
    assert set(row) == {
        "slug", "name", "item_type", "short_desc", "price", "made_to_order", "lead_time_days",
        "in_stock", "wood_type", "dimensions_mm", "category", "images", "renders",
        "model_url",
    }  # fmt: skip
    assert "purchase_price" not in row and "production_cost" not in row
    assert "current_stock" not in row and "sku" not in row
    assert row["in_stock"] is True


def test_publishing_makes_a_unique_slug_and_needs_a_price(auth_client: TestClient):
    a = make_item(auth_client, name="Rosewood Wall Panel")
    b = make_item(auth_client, name="Rosewood Wall Panel")
    assert a["slug"] == "rosewood-wall-panel" and b["slug"] == "rosewood-wall-panel-2"

    r = auth_client.post(
        "/items/",
        json={"sku": "NOPRICE", "name": "Free Thing", "is_published": True},
    )
    assert r.status_code == 400 and "price" in r.json()["detail"]

    dup = auth_client.post(
        "/items/",
        json={"sku": "DUPSLUG", "name": "Other", "slug": a["slug"], "selling_price": 5},
    )
    assert dup.status_code == 400
    bad = auth_client.post(
        "/items/", json={"sku": "BADSLUG", "name": "X", "slug": "Not A Slug!"}
    )
    assert bad.status_code == 422


def test_unpublished_product_is_404(auth_client: TestClient):
    item = make_item(auth_client, is_published=False, slug="secret-chair")
    assert auth_client.get("/public/catalog/secret-chair").status_code == 404
    assert item["slug"] == "secret-chair"


def test_catalog_search_and_category_filter(auth_client: TestClient, db):
    cat = Category(name=f"Carvings-{uuid.uuid4().hex[:6]}")
    db.add(cat)
    db.commit()
    carved = make_item(auth_client, name="Zzq Door Frame", category_id=str(cat.id))
    make_item(auth_client, name="Zzq Plain Table")

    by_cat = auth_client.get("/public/catalog", params={"category": cat.name}).json()
    assert [r["slug"] for r in by_cat] == [carved["slug"]]
    assert by_cat[0]["category"] == cat.name
    by_q = auth_client.get("/public/catalog", params={"q": "zzq plain"}).json()
    assert [r["name"] for r in by_q] == ["Zzq Plain Table"]


def test_public_endpoints_need_no_login(auth_client: TestClient):
    item = make_item(auth_client)
    app.dependency_overrides.pop(get_current_user)  # now anonymous
    assert auth_client.get(f"/public/catalog/{item['slug']}").status_code == 200
    assert auth_client.get("/items/").status_code == 401


# ------------------------------------------------------------------ checkout
def test_checkout_creates_a_draft_web_order_at_server_prices(auth_client: TestClient):
    item = make_item(auth_client, selling_price=18500)
    body = checkout_body(item["slug"], qty=2)
    body["items"][0]["price"] = 1  # a client-supplied price must be ignored

    r = auth_client.post("/public/checkout", json=body)
    assert r.status_code == 201, r.text
    out = r.json()
    assert out["status"] == "draft" and out["total"] == "37000.00"
    assert out["advance_paid"] == "0.00" and out["balance_due"] == "37000.00"
    assert out["items"][0]["unit_price"] == "18500.00"
    assert out["order_number"].startswith("ORD-")

    staff = auth_client.get("/orders/", params={"source": "web"}).json()
    mine = next(o for o in staff if o["order_number"] == out["order_number"])
    assert mine["source"] == "web" and mine["created_by"] is None
    assert mine["delivery_address"]["pincode"] == "574201"
    one = auth_client.get(f"/orders/{mine['id']}")
    assert one.status_code == 200  # a guest order serialises without a staff user
    # stock is only checked, not taken: staff confirm and bill later
    assert auth_client.get(f"/items/{item['id']}").json()["current_stock"] == 4


def test_checkout_reuses_the_customer_by_phone(auth_client: TestClient):
    item = make_item(auth_client)
    a = auth_client.post("/public/checkout", json=checkout_body(item["slug"]))
    b = auth_client.post(
        "/public/checkout",
        json=checkout_body(item["slug"], phone="+91 98765 00011", name="Meena K"),
    )
    assert a.status_code == b.status_code == 201
    orders = {
        o["order_number"]: o["customer_id"]
        for o in auth_client.get("/orders/", params={"source": "web"}).json()
    }
    assert orders[a.json()["order_number"]] == orders[b.json()["order_number"]]


def test_checkout_stock_and_made_to_order_rules(auth_client: TestClient):
    stocked = make_item(auth_client, current_stock=1)
    bespoke = make_item(auth_client, current_stock=0, made_to_order=True)

    over = auth_client.post("/public/checkout", json=checkout_body(stocked["slug"], 2))
    assert over.status_code == 400 and "stock" in over.json()["detail"]
    ok = auth_client.post("/public/checkout", json=checkout_body(bespoke["slug"], 3))
    assert ok.status_code == 201  # made to order needs no stock
    cod = auth_client.post(
        "/public/checkout",
        json=checkout_body(bespoke["slug"], pay_option="on_delivery"),
    )
    assert cod.status_code == 400 and "advance" in cod.json()["detail"]
    cod_ok = auth_client.post(
        "/public/checkout",
        json=checkout_body(stocked["slug"], pay_option="on_delivery"),
    )
    assert cod_ok.status_code == 201


def test_checkout_rejects_unknown_or_unpublished_products(auth_client: TestClient):
    hidden = make_item(auth_client, is_published=False, slug="not-for-sale")
    for slug in ("no-such-thing", hidden["slug"]):
        r = auth_client.post("/public/checkout", json=checkout_body(slug))
        assert r.status_code == 400


def test_checkout_validates_phone_and_fields(auth_client: TestClient):
    item = make_item(auth_client)
    assert (
        auth_client.post(
            "/public/checkout", json=checkout_body(item["slug"], phone="12345")
        ).status_code
        == 422
    )
    assert (
        auth_client.post(
            "/public/checkout", json=checkout_body(item["slug"], pincode="57")
        ).status_code
        == 422
    )
    assert (
        auth_client.post(
            "/public/checkout", json={**checkout_body(item["slug"]), "items": []}
        ).status_code
        == 422
    )


def test_delivery_pincode_allow_list(auth_client: TestClient, monkeypatch):
    item = make_item(auth_client)
    assert auth_client.get(
        "/public/delivery-check", params={"pincode": "110001"}
    ).json()[
        "serviceable"
    ]  # no list configured yet: no restriction
    monkeypatch.setattr(settings, "DELIVERY_PINCODES", ["574201"])
    yes = auth_client.get("/public/delivery-check", params={"pincode": "574201"})
    no = auth_client.get("/public/delivery-check", params={"pincode": "560001"})
    assert yes.json()["serviceable"] is True and no.json()["serviceable"] is False
    blocked = auth_client.post(
        "/public/checkout", json=checkout_body(item["slug"], pincode="560001")
    )
    assert blocked.status_code == 400 and "deliver" in blocked.json()["detail"]
    assert (
        auth_client.post(
            "/public/checkout", json=checkout_body(item["slug"])
        ).status_code
        == 201
    )


# ------------------------------------------------------------------ enquiry + tracking
def test_enquiry_creates_a_custom_draft_order(auth_client: TestClient):
    r = auth_client.post(
        "/public/enquiry",
        json={
            "name": "Anitha",
            "phone": "9000000042",
            "what": "Temple door frame",
            "description": "Teak, 7ft x 3ft, floral border",
            "wood": "Teak",
            "size": "2100 x 900",
        },
    )
    assert r.status_code == 201, r.text
    num = r.json()["order_number"]
    staff = next(
        o
        for o in auth_client.get("/orders/", params={"source": "web"}).json()
        if o["order_number"] == num
    )
    assert staff["order_type"] == "custom" and staff["status"] == "draft"
    assert staff["custom_specs"]["wood"] == "Teak"


def test_tracking_needs_the_matching_phone(auth_client: TestClient):
    item = make_item(auth_client)
    num = auth_client.post("/public/checkout", json=checkout_body(item["slug"])).json()[
        "order_number"
    ]

    ok = auth_client.get(f"/public/orders/{num}", params={"phone": PHONE})
    assert ok.status_code == 200 and ok.json()["order_number"] == num
    assert (
        auth_client.get(
            f"/public/orders/{num.lower()}", params={"phone": "+91-98765-00011"}
        ).status_code
        == 200
    )
    for phone in ("9876500099", "garbage"):
        wrong = auth_client.get(f"/public/orders/{num}", params={"phone": phone})
        assert wrong.status_code == 404
    assert (
        auth_client.get(
            "/public/orders/ORD-2099-9999", params={"phone": PHONE}
        ).status_code
        == 404
    )  # same answer as a wrong phone: numbers can't be probed


def test_tracking_shows_progress_and_balance(auth_client: TestClient):
    item = make_item(auth_client, selling_price=10000)
    order = auth_client.post(
        "/public/checkout", json=checkout_body(item["slug"])
    ).json()
    staff_id = next(
        o["id"]
        for o in auth_client.get("/orders/", params={"source": "web"}).json()
        if o["order_number"] == order["order_number"]
    )
    assert (
        auth_client.patch(
            f"/orders/{staff_id}/status", json={"status": "confirmed"}
        ).status_code
        == 200
    )
    seen = auth_client.get(
        f"/public/orders/{order['order_number']}", params={"phone": PHONE}
    ).json()
    assert seen["status"] == "confirmed" and seen["balance_due"] == "10000.00"


# ------------------------------------------------------------------ rate limit
def test_public_writes_are_rate_limited(auth_client: TestClient, monkeypatch):
    monkeypatch.setattr(settings, "PUBLIC_RATE_LIMIT", 2)
    body = {
        "name": "Spam",
        "phone": "9000000077",
        "what": "Thing",
        "description": "Please build it",
    }
    codes = [
        auth_client.post("/public/enquiry", json=body).status_code for _ in range(4)
    ]
    assert codes == [201, 201, 429, 429]


# ------------------------------------------------------------------ uploads
def upload(client, item_id, data, content_type, name="f", **form):
    return client.post(
        f"/items/{item_id}/assets",
        files={"file": (name, data, content_type)},
        data=form,
    )


def test_photo_upload_is_listed_served_and_public(auth_client: TestClient):
    item = make_item(auth_client)
    r = upload(auth_client, item["id"], png_bytes(), "image/png", alt="Front view")
    assert r.status_code == 201, r.text
    asset = r.json()
    assert asset["kind"] == "image" and asset["url"].startswith("/media/items/")

    served = auth_client.get(asset["url"])
    assert served.status_code == 200 and served.headers["content-type"] == "image/png"
    assert "immutable" in served.headers["cache-control"]
    assert auth_client.head(asset["url"]).status_code == 200
    assert served.headers["x-content-type-options"] == "nosniff"

    prod = auth_client.get(f"/public/catalog/{item['slug']}").json()
    assert prod["images"] == [{"url": asset["url"], "alt": "Front view"}]
    assert prod["model_url"] is None
    assert [a["id"] for a in auth_client.get(f"/items/{item['id']}/assets").json()] == [
        asset["id"]
    ]


def test_glb_model_becomes_the_3d_url(auth_client: TestClient):
    item = make_item(auth_client)
    r = upload(auth_client, item["id"], GLB, "model/gltf-binary")
    assert r.status_code == 201 and r.json()["kind"] == "glb"
    prod = auth_client.get(f"/public/catalog/{item['slug']}").json()
    assert prod["model_url"] == r.json()["url"] and prod["images"] == []
    assert (
        auth_client.get(prod["model_url"]).headers["content-type"]
        == "model/gltf-binary"
    )


def test_uploads_are_validated(auth_client: TestClient, monkeypatch):
    item = make_item(auth_client)
    iid = item["id"]
    assert upload(auth_client, iid, b"hello", "text/plain").status_code == 415
    assert upload(auth_client, iid, b"", "image/png").status_code == 400
    assert upload(auth_client, iid, b"not an image", "image/png").status_code == 400
    # real PNG bytes, but declared as JPEG: content must match its type
    assert upload(auth_client, iid, png_bytes(), "image/jpeg").status_code == 400
    assert upload(auth_client, iid, b"nope" * 8, "model/gltf-binary").status_code == 400
    monkeypatch.setitem(media_service.MAX_BYTES, "image", 10)
    assert upload(auth_client, iid, png_bytes(), "image/png").status_code == 413
    assert auth_client.get(f"/items/{iid}/assets").json() == []  # nothing was kept


def test_delete_asset_removes_the_file(auth_client: TestClient):
    item = make_item(auth_client)
    asset = upload(auth_client, item["id"], png_bytes(), "image/png").json()
    assert auth_client.get(asset["url"]).status_code == 200
    assert (
        auth_client.delete(f"/items/{item['id']}/assets/{asset['id']}").status_code
        == 204
    )
    assert auth_client.get(asset["url"]).status_code == 404
    assert (
        auth_client.delete(f"/items/{item['id']}/assets/{asset['id']}").status_code
        == 404
    )


def test_media_route_blocks_path_escapes(auth_client: TestClient):
    for bad in ("../secret.png", "items/../../x.png", "%2e%2e/x.png", "nothing.png"):
        assert auth_client.get(f"/media/{bad}").status_code == 404


def test_only_owner_or_manager_can_upload(worker_client: TestClient, db):
    # worker_client has no items API access, so use any id: the role check comes first
    r = upload(worker_client, str(uuid.uuid4()), png_bytes(), "image/png")
    assert r.status_code == 403
    assert worker_client.get(f"/items/{uuid.uuid4()}/assets").status_code == 403


def test_upload_to_unknown_item_is_404(auth_client: TestClient):
    assert (
        upload(auth_client, str(uuid.uuid4()), png_bytes(), "image/png").status_code
        == 404
    )


def test_media_route_function_rejects_escapes_directly(tmp_path):
    """The HTTP client normalises ../ away, so call the route function itself."""
    from fastapi import HTTPException

    from app.api.media import get_media

    (tmp_path / "secret.png").write_bytes(b"x")  # one level above MEDIA_DIR
    for bad in ("../secret.png", "items/../../secret.png", "/etc/passwd"):
        with pytest.raises(HTTPException) as e:
            get_media(bad)
        assert e.value.status_code == 404
