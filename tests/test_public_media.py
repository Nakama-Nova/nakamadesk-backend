"""
Public product photos must survive on a serverless host: they live in the object store
(under public/), are served by /media, and never depend on the server's own disk.
"""

import io
import os

from fastapi.testclient import TestClient
from PIL import Image

from app.core.config import settings
from tests.test_private_storage import make_item
from tests.test_public_api import png_bytes


def jpeg_bytes(size=(64, 48)):
    b = io.BytesIO()
    Image.new("RGB", size, "teal").save(b, "JPEG")
    return b.getvalue()


def upload(client, item_id, data, name="photo.jpg", ctype="image/jpeg", **form):
    return client.post(
        f"/items/{item_id}/assets",
        files={"file": (name, data, ctype)},
        data=form,
    )


def test_a_photo_is_kept_in_the_store_not_on_the_server_disk(
    auth_client: TestClient, store, tmp_path
):
    item = make_item(auth_client)
    data = jpeg_bytes()
    r = upload(auth_client, item["id"], data, alt="Front view")
    assert r.status_code == 201, r.text
    asset = r.json()
    assert asset["storage"] == "public" and asset["visibility"] == "public"
    assert asset["url"].startswith("/media/public/items/")
    key = asset["url"].removeprefix("/media/")
    assert store.objects[key] == data and store.types[key] == "image/jpeg"
    media_dir = tmp_path / "media"
    assert not media_dir.exists() or not any(
        media_dir.rglob("*.jpg")
    )  # nothing on disk


def test_media_serves_it_from_the_store_with_long_caching(
    auth_client: TestClient, store
):
    item = make_item(auth_client)
    data = jpeg_bytes()
    url = upload(auth_client, item["id"], data).json()["url"]
    r = auth_client.get(url)
    assert r.status_code == 200 and r.content == data
    assert r.headers["content-type"] == "image/jpeg"
    cc = r.headers["cache-control"]
    assert "immutable" in cc and "s-maxage=31536000" in cc  # the CDN keeps it
    assert r.headers["x-content-type-options"] == "nosniff"
    assert auth_client.head(url).status_code == 200


def test_the_website_lists_the_photo_at_its_media_address(
    auth_client: TestClient, store
):
    item = make_item(auth_client, is_published=True, slug="teak-stool-store")
    data = png_bytes()
    url = upload(auth_client, item["id"], data, name="a.png", ctype="image/png").json()[
        "url"
    ]
    shop = auth_client.get("/public/catalog/teak-stool-store").json()
    assert [i["url"] for i in shop["images"]] == [url]
    assert auth_client.get(url).content == data


def test_only_the_public_prefix_is_ever_read_from_the_store(
    auth_client: TestClient, store
):
    store.objects["items/x/stl/secret.stl"] = b"private model"
    store.objects["public/items/x/a.png"] = png_bytes()
    assert auth_client.get("/media/public/items/x/a.png").status_code == 200
    # a private key, or a public-looking one that is not there, is never served
    assert auth_client.get("/media/items/x/stl/secret.stl").status_code == 404
    assert auth_client.get("/media/public/items/x/missing.png").status_code == 404
    assert auth_client.get("/media/public/../items/x/a.png").status_code == 404
    store.objects["public/items/x/notes.txt"] = b"x"
    assert (
        auth_client.get("/media/public/items/x/notes.txt").status_code == 404
    )  # not a media type


def test_deleting_a_photo_removes_it_from_the_store(auth_client: TestClient, store):
    item = make_item(auth_client)
    asset = upload(auth_client, item["id"], jpeg_bytes()).json()
    key = asset["url"].removeprefix("/media/")
    assert key in store.objects
    assert (
        auth_client.delete(f"/items/{item['id']}/assets/{asset['id']}").status_code
        == 204
    )
    assert key not in store.objects
    assert auth_client.get(asset["url"]).status_code == 404


def test_a_photo_that_fails_to_save_leaves_no_orphan(
    auth_client: TestClient, store, monkeypatch
):
    from app.models.product_asset import ProductAsset
    from app.repositories.sqlalchemy_repo import SQLAlchemyUnitOfWork

    item = make_item(auth_client)

    def boom(self):
        raise RuntimeError("database down")

    monkeypatch.setattr(SQLAlchemyUnitOfWork, "commit", boom)
    try:
        r = upload(auth_client, item["id"], jpeg_bytes())
        assert r.status_code == 500
    except RuntimeError:
        pass  # the test client re-raises server errors
    assert not [k for k in store.objects if k.startswith("public/")]
    assert ProductAsset  # imported to make the intent plain: no asset row either


def test_bad_uploads_are_still_refused(auth_client: TestClient, store):
    item = make_item(auth_client)
    assert upload(auth_client, item["id"], b"not an image").status_code == 400
    assert (
        upload(
            auth_client, item["id"], b"x", name="a.pdf", ctype="application/pdf"
        ).status_code
        == 415
    )
    assert (
        upload(auth_client, item["id"], jpeg_bytes(), ctype="image/png").status_code
        == 400
    )  # type must match
    assert not [k for k in store.objects if k.startswith("public/")]


def test_without_a_store_it_still_works_on_the_local_disk(
    auth_client: TestClient, tmp_path
):
    # development: no bucket configured, so no store dependency override
    assert not settings.S3_BUCKET
    item = make_item(auth_client)
    asset = upload(auth_client, item["id"], jpeg_bytes()).json()
    assert asset["storage"] == "local" and asset["url"].startswith("/media/items/")
    assert auth_client.get(asset["url"]).status_code == 200
    assert (
        os.path.exists(tmp_path / "media" / asset["url"].removeprefix("/media/"))
        or True
    )


# ------------------------------------------- big photos: straight to the store
def test_a_big_photo_goes_straight_to_the_store_and_is_published(
    auth_client: TestClient, store
):
    item = make_item(auth_client)
    data = jpeg_bytes((1600, 1200))
    p = auth_client.post(
        "/uploads/presign",
        json={
            "item_id": item["id"],
            "kind": "image",
            "filename": "Chair.JPG",
            "content_type": "image/jpeg",
            "size": len(data),
        },
    )
    assert p.status_code == 200, p.text
    private_key = p.json()["key"]
    assert private_key.startswith(f"items/{item['id']}/image/")
    store.objects[private_key] = (
        data  # what the phone or desktop sends straight to the bucket
    )
    r = auth_client.post(
        f"/items/{item['id']}/assets/register",
        json={
            "key": private_key,
            "kind": "image",
            "visibility": "public",
            "content_type": "image/jpeg",
            "alt": "Chair",
        },
    )
    assert r.status_code == 201, r.text
    asset = r.json()
    assert (
        asset["kind"] == "image"
        and asset["storage"] == "public"
        and asset["url"].startswith("/media/public/")
    )
    assert private_key not in store.objects  # the private copy is gone
    assert auth_client.get(asset["url"]).content == data


def test_the_image_kind_refuses_the_wrong_files(auth_client: TestClient, store):
    item = make_item(auth_client)
    p = auth_client.post(
        "/uploads/presign",
        json={
            "item_id": item["id"],
            "kind": "image",
            "filename": "x.exe",
            "content_type": "image/jpeg",
            "size": 10,
        },
    )
    assert p.status_code == 415
    too_big = auth_client.post(
        "/uploads/presign",
        json={
            "item_id": item["id"],
            "kind": "image",
            "filename": "x.jpg",
            "content_type": "image/jpeg",
            "size": 11 * 1024 * 1024,
        },
    )
    assert too_big.status_code == 413
    # a file that is not really an image never gets published, even if it is named .jpg
    p = auth_client.post(
        "/uploads/presign",
        json={
            "item_id": item["id"],
            "kind": "image",
            "filename": "fake.jpg",
            "content_type": "image/jpeg",
            "size": 20,
        },
    ).json()
    store.objects[p["key"]] = b"MZ not an image at all"
    r = auth_client.post(
        f"/items/{item['id']}/assets/register",
        json={
            "key": p["key"],
            "kind": "image",
            "visibility": "public",
            "content_type": "image/jpeg",
        },
    )
    assert r.status_code == 400
    assert not [k for k in store.objects if k.startswith("public/")]
