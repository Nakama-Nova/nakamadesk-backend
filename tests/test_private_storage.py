"""Private object store + visibility: staff-only files must never reach the website."""

import io
import json
import uuid

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.main import app
from app.services import media_service
from app.services.object_store import get_store

_png = io.BytesIO()
Image.new("RGB", (8, 8), "red").save(_png, "PNG")
PNG = _png.getvalue()
GLB = b"glTF" + b"\x02\x00\x00\x00" + b"\x00" * 16


def make_item(client, **over):
    body = {
        "sku": f"PRV-{uuid.uuid4().hex[:8]}",
        "name": "Dining Chair",
        "selling_price": 5000,
        "is_published": True,
        **over,
    }
    r = client.post("/items/", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def put_object(store, item_id, kind, ext, data):
    """What the client does after /uploads/presign: send bytes straight to the store."""
    key = f"items/{item_id}/{kind}/{uuid.uuid4().hex}{ext}"
    store.objects[key] = data
    return key


def register(client, item_id, key, kind, visibility="staff"):
    return client.post(
        f"/items/{item_id}/assets/register",
        json={
            "key": key,
            "kind": kind,
            "visibility": visibility,
            "content_type": "application/octet-stream",
        },
    )


# ----------------------------------------------------------------- presign
def test_presign_returns_a_policy_with_size_and_type_limits(
    auth_client: TestClient, store
):
    item = make_item(auth_client)
    r = auth_client.post(
        "/uploads/presign",
        json={
            "item_id": item["id"],
            "kind": "stl",
            "filename": "Chair.STL",
            "content_type": "model/stl",
            "size": 5_000_000,
        },
    )
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["key"].startswith(f"items/{item['id']}/stl/")
    assert out["key"].endswith(".stl")
    assert out["method"] == "PUT"
    assert out["headers"] == {"Content-Type": "model/stl"}
    assert store.signed[-1][1:] == ("model/stl", 5_000_000)  # type and size are signed
    assert out["max_bytes"] == 300 * 1024 * 1024


@pytest.mark.parametrize(
    "kind,filename,size,code",
    [
        ("stl", "a.exe", 100, 415),
        ("nope", "a.stl", 100, 400),
        ("stl", "a.stl", 0, 413),
        ("render", "a.png", 16 * 1024 * 1024, 413),
        ("nc", "a.nc", 51 * 1024 * 1024, 413),
    ],
)
def test_presign_rejects_bad_requests(
    auth_client: TestClient, store, kind, filename, size, code
):
    item = make_item(auth_client)
    r = auth_client.post(
        "/uploads/presign",
        json={
            "item_id": item["id"],
            "kind": kind,
            "filename": filename,
            "content_type": "application/octet-stream",
            "size": size,
        },
    )
    assert r.status_code == code


def test_presign_unknown_item_and_unconfigured_store(auth_client: TestClient, store):
    body = {
        "item_id": str(uuid.uuid4()),
        "kind": "stl",
        "filename": "a.stl",
        "content_type": "model/stl",
        "size": 10,
    }
    assert auth_client.post("/uploads/presign", json=body).status_code == 404
    app.dependency_overrides.pop(get_store)  # real dependency again: no bucket set
    assert auth_client.post("/uploads/presign", json=body).status_code == 503


# ----------------------------------------------------------------- register
def test_register_keeps_a_staff_file_private(auth_client: TestClient, store):
    item = make_item(auth_client)
    key = put_object(store, item["id"], "stl", ".stl", b"x" * 1000)
    r = register(auth_client, item["id"], key, "stl")
    assert r.status_code == 201, r.text
    a = r.json()
    assert (a["visibility"], a["storage"], a["kind"]) == ("staff", "s3", "stl")
    assert a["url"] == f"/items/{item['id']}/assets/{a['id']}/download"

    dl = auth_client.get(a["url"], follow_redirects=False)
    assert dl.status_code in (302, 307)
    assert dl.headers["location"].startswith("https://store.invalid/")


def test_register_checks_the_object_and_the_key(auth_client: TestClient, store):
    item, other = make_item(auth_client), make_item(auth_client)
    missing = f"items/{item['id']}/stl/nothing.stl"
    assert register(auth_client, item["id"], missing, "stl").status_code == 409
    foreign = put_object(store, other["id"], "stl", ".stl", b"x")
    assert register(auth_client, item["id"], foreign, "stl").status_code == 400
    mine = put_object(store, item["id"], "stl", ".stl", b"x")
    assert register(auth_client, item["id"], mine, "obj").status_code == 400
    assert register(auth_client, item["id"], mine, "mystery").status_code == 400
    big = put_object(store, item["id"], "nc", ".nc", b"x" * (51 * 1024 * 1024))
    assert register(auth_client, item["id"], big, "nc").status_code == 413
    assert big not in store.objects  # an oversize upload is deleted, not kept


def test_only_render_and_glb_can_be_published(auth_client: TestClient, store):
    item = make_item(auth_client)
    for kind, ext in (
        ("stl", ".stl"),
        ("nc", ".nc"),
        ("source_scan", ".zip"),
        ("part_model", ".glb"),
        ("ply", ".ply"),
    ):
        key = put_object(store, item["id"], kind, ext, b"data")
        r = register(auth_client, item["id"], key, kind, visibility="public")
        assert r.status_code == 400, kind


def test_publishing_copies_to_public_storage(auth_client: TestClient, store):
    item = make_item(auth_client)
    key = put_object(store, item["id"], "render", ".png", PNG)
    r = register(auth_client, item["id"], key, "render", visibility="public")
    assert r.status_code == 201, r.text
    a = r.json()
    assert (a["visibility"], a["storage"]) == ("public", "public")
    assert a["url"].startswith(
        "/media/public/"
    )  # kept in the store, not on the server disk
    assert key not in store.objects  # the private copy is gone
    assert auth_client.get(a["url"]).headers["content-type"] == "image/png"


def test_publishing_validates_the_file_content(auth_client: TestClient, store):
    item = make_item(auth_client)
    fake_png = put_object(store, item["id"], "render", ".png", b"not an image")
    r = register(auth_client, item["id"], fake_png, "render", visibility="public")
    assert r.status_code == 400
    fake_glb = put_object(store, item["id"], "glb", ".glb", b"nope" * 8)
    assert (
        register(
            auth_client, item["id"], fake_glb, "glb", visibility="public"
        ).status_code
        == 400
    )
    # the same bytes are fine as staff-only (nothing is shown to anyone)
    assert (
        register(
            auth_client, item["id"], fake_glb, "glb", visibility="staff"
        ).status_code
        == 201
    )


def test_only_owner_and_manager_can_register(worker_client: TestClient, store):
    key = f"items/{uuid.uuid4()}/stl/x.stl"
    assert register(worker_client, str(uuid.uuid4()), key, "stl").status_code == 403


def test_deleting_an_s3_asset_deletes_the_object(auth_client: TestClient, store):
    item = make_item(auth_client)
    key = put_object(store, item["id"], "stl", ".stl", b"x" * 10)
    asset = register(auth_client, item["id"], key, "stl").json()
    assert key in store.objects
    r = auth_client.delete(f"/items/{item['id']}/assets/{asset['id']}")
    assert r.status_code == 204
    assert key not in store.objects


# ----------------------------------------------------------------- THE leak test
def test_staff_only_files_never_appear_on_the_public_website(
    auth_client: TestClient, store
):
    item = make_item(auth_client, slug="leak-test-chair")
    secrets = []
    for kind, ext, data in (
        ("stl", ".stl", b"s" * 50),
        ("obj", ".obj", b"o" * 50),
        ("ply", ".ply", b"p" * 50),
        ("nc", ".nc", b"G0 X0"),
        ("source_scan", ".zip", b"z" * 50),
        ("part_model", ".glb", GLB),
        ("glb", ".glb", GLB),  # a staff-only finished model stays hidden too
    ):
        key = put_object(store, item["id"], kind, ext, data)
        a = register(auth_client, item["id"], key, kind, visibility="staff").json()
        secrets += [key, key.rsplit("/", 1)[-1], a["id"], f"/{kind}/"]

    public_key = put_object(store, item["id"], "render", ".png", PNG)
    public = register(
        auth_client, item["id"], public_key, "render", visibility="public"
    ).json()

    pages = [
        auth_client.get("/public/catalog").text,
        auth_client.get(f"/public/catalog/{item['slug']}").text,
        auth_client.get("/public/catalog", params={"q": "chair"}).text,
    ]
    product = json.loads(pages[1])
    assert product["renders"] == [{"url": public["url"], "alt": None}]
    assert product["images"] == [] and product["model_url"] is None
    for page in pages:
        for secret in secrets:
            assert secret not in page, secret


def test_assets_created_without_saying_so_are_staff_only(
    db, auth_client: TestClient, store
):
    """Safe by default: the column default is 'staff'."""
    from app.models.product_asset import ProductAsset

    item = make_item(auth_client)
    asset = ProductAsset(
        item_id=uuid.UUID(item["id"]),
        kind="image",
        storage_key="items/x/y.png",
        content_type="image/png",
        bytes=1,
    )
    db.add(asset)
    db.commit()
    db.refresh(asset)
    assert (asset.visibility, asset.storage) == ("staff", "local")
    assert "items/x/y.png" not in auth_client.get("/public/catalog").text


def test_existing_photo_upload_is_still_public(auth_client: TestClient, store):
    item = make_item(auth_client)
    r = auth_client.post(
        f"/items/{item['id']}/assets", files={"file": ("p.png", PNG, "image/png")}
    )
    assert r.status_code == 201 and r.json()["visibility"] == "public"
    assert len(auth_client.get(f"/public/catalog/{item['slug']}").json()["images"]) == 1


def test_private_kind_table_is_sane():
    assert media_service.can_be_public("render")
    assert media_service.can_be_public("glb")
    for kind in ("stl", "obj", "ply", "nc", "source_scan", "part_model"):
        assert not media_service.can_be_public(kind)
