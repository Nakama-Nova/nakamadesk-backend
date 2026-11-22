"""
Admin commands that must work before anyone can log in.

    python -m app.cli create-owner
    python -m app.cli finance-sync      write the ledger from sales and order payments
    python -m app.cli optimise-photos   show which published photos are oversized (changes nothing)
    python -m app.cli optimise-photos --apply   shrink them (WebP, at most 2000 px) and keep the new copies
"""

import getpass
import sys

from fastapi import HTTPException

from app.db.session import SessionLocal
from app.services import auth_service


def create_owner() -> int:
    username = input("Owner username: ").strip()
    email = input("Owner email: ").strip()
    password = getpass.getpass("Password: ")
    if password != getpass.getpass("Repeat password: "):
        print("Passwords do not match.")
        return 1

    db = SessionLocal()
    try:
        user = auth_service.bootstrap_owner(db, username, email, password)
    except HTTPException as e:
        print(f"Not created: {e.detail}")
        return 1
    finally:
        db.close()
    print(f"Owner '{user.username}' created.")
    return 0


def finance_sync() -> int:
    from app.finance import projector

    db = SessionLocal()
    try:
        r = projector.project(db)
    finally:
        db.close()
    print(
        f"posted {r.posted}, reversed {r.reversed}, unchanged {r.unchanged}, "
        f"nothing to post {r.nothing_to_post}, problems {len(r.problems)}"
    )
    return 0


def optimise_photos(apply: bool) -> int:
    """
    Shrink photos that were published before uploads were optimised. Each one is re-saved as a web-sized
    copy under a new address and the database row is pointed at it; the old file is deleted last, so a
    failure part-way never leaves a photo without a file. Without --apply it only reports.
    """
    from app.models.product_asset import ProductAsset
    from app.services import media_service
    from app.services.object_store import get_store_or_none

    store = get_store_or_none()
    db = SessionLocal()
    saved = changed = 0
    try:
        rows = (
            db.query(ProductAsset)
            .filter(
                ProductAsset.visibility == "public",
                ProductAsset.kind.in_(("image", "render")),
                ProductAsset.storage.in_(media_service.MEDIA_STORAGES),
            )
            .all()
        )
        for a in rows:
            ext = a.storage_key.rsplit(".", 1)[-1].lower()
            if ext == "jpeg":
                ext = "jpg"
            if ext not in ("jpg", "png", "webp"):
                continue
            try:
                if a.storage == "public":
                    if store is None:
                        continue
                    data = store.get_bytes(a.storage_key, 30 * media_service.MB)
                else:
                    data = media_service._local_path(a.storage_key).read_bytes()
            except Exception as e:  # noqa: BLE001
                print(f"skip {a.storage_key}: cannot read ({e})")
                continue
            new_ext, new_data, new_type = media_service.optimise_image(ext, data)
            if new_data is data or (new_ext == ext and len(new_data) >= len(data)):
                continue
            changed += 1
            saved += len(data) - len(new_data)
            print(
                f"{a.storage_key}: {len(data) // 1024} KB -> {len(new_data) // 1024} KB ({new_ext})"
            )
            if not apply:
                continue
            old_key, old_storage = a.storage_key, a.storage
            new_key, new_storage = media_service.save_public(
                a.item_id, new_ext, new_data, store
            )
            try:
                a.storage_key, a.storage = new_key, new_storage
                a.content_type, a.bytes = new_type, len(new_data)
                db.commit()
            except Exception:
                db.rollback()
                media_service.delete(new_key, new_storage, store)
                raise
            media_service.delete(old_key, old_storage, store)
    finally:
        db.close()
    verb = "saved" if apply else "would save"
    print(f"{changed} photo(s) to shrink; {verb} {saved // 1024} KB")
    if changed and not apply:
        print("Run again with --apply to do it.")
    return 0


if __name__ == "__main__":
    if sys.argv[1:] in (["optimise-photos"], ["optimise-photos", "--apply"]):
        sys.exit(optimise_photos(apply="--apply" in sys.argv))
    if sys.argv[1:] == ["finance-sync"]:
        sys.exit(finance_sync())
    if sys.argv[1:] == ["create-owner"]:
        sys.exit(create_owner())
    print(__doc__)
    sys.exit(2)
