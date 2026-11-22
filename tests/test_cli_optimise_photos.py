"""`python -m app.cli optimise-photos` shrinks photos published before uploads were optimised."""

import io
import os

from PIL import Image

from app import cli
from app.db.session import SessionLocal
from app.models.product_asset import ProductAsset
from app.services import media_service
from tests.test_private_storage import make_item


def test_dry_run_changes_nothing_and_apply_shrinks_the_old_photo(
    auth_client, store, monkeypatch
):
    item = make_item(auth_client)
    big = io.BytesIO()
    Image.frombytes("RGB", (2100, 1500), os.urandom(2100 * 1500 * 3)).save(big, "PNG")
    data = big.getvalue()
    # a photo published the old way: straight into the store, as uploaded
    key, storage = media_service.save_public(item["id"], "png", data, store)
    with SessionLocal() as db:
        db.add(
            ProductAsset(
                item_id=item["id"],
                kind="image",
                storage_key=key,
                content_type="image/png",
                bytes=len(data),
                visibility="public",
                storage=storage,
            )
        )
        db.commit()
    monkeypatch.setattr("app.services.object_store.get_store_or_none", lambda: store)

    assert cli.optimise_photos(apply=False) == 0
    assert store.objects[key] == data  # untouched

    assert cli.optimise_photos(apply=True) == 0
    with SessionLocal() as db:
        a = db.query(ProductAsset).filter(ProductAsset.item_id == item["id"]).one()
        assert a.storage_key != key and a.storage_key.endswith(".webp")
        assert a.content_type == "image/webp" and a.bytes < len(data) / 3
        assert a.bytes == len(store.objects[a.storage_key])
    assert key not in store.objects  # the old file is gone
