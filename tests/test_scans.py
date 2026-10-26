"""Scan jobs: phone capture -> queue -> workshop PC -> results attached to the product."""

import io
import uuid
from datetime import datetime, timedelta, timezone

from PIL import Image

from app.models.scan_job import ScanJob

PC = "workshop-pc"


def png_bytes():
    b = io.BytesIO()
    Image.new("RGB", (8, 8), "blue").save(b, "PNG")
    return b.getvalue()


def make_item(client, h):
    r = client.post(
        "/items/",
        headers=h,
        json={"sku": f"SC-{uuid.uuid4().hex[:6]}", "name": "Carved Chair", "selling_price": 9000, "is_published": True},
    )  # fmt: skip
    assert r.status_code == 200, r.text
    return r.json()


def capture(client, h, store, photos=10, item_id=None, kind="photos"):
    """What the phone does: create, presign, upload."""
    scan = client.post(
        "/scans", headers=h, json={"item_id": item_id, "source_kind": kind}
    ).json()
    ext, ctype = (".jpg", "image/jpeg") if kind == "photos" else (".mp4", "video/mp4")
    r = client.post(
        f"/scans/{scan['id']}/presign",
        headers=h,
        json={"files": [{"filename": f"{i}{ext}", "content_type": ctype, "size": 1000} for i in range(photos)]},
    )  # fmt: skip
    assert r.status_code == 200, r.text
    keys = [f["key"] for f in r.json()["files"]]
    for k in keys:
        store.objects[k] = b"x" * 1000
    return scan, keys


def queue(client, h, store, **kw):
    scan, keys = capture(client, h, store, **kw)
    r = client.post(f"/scans/{scan['id']}/complete", headers=h, json={"keys": keys})
    assert r.status_code == 200, r.text
    return r.json()


def claim(client, h, scan_id, worker=PC):
    return client.post(f"/scans/{scan_id}/claim", headers=h, json={"worker": worker})


def put_result(client, h, store, scan_id, kind, filename, data=b"result", worker=PC):
    r = client.post(
        f"/scans/{scan_id}/results/presign",
        headers=h,
        json={"worker": worker, "kind": kind, "filename": filename, "content_type": "application/octet-stream", "size": len(data)},
    )  # fmt: skip
    assert r.status_code == 200, r.text
    key = r.json()["key"]
    store.objects[key] = data
    return key


# ---------------------------------------------------------------- the whole journey
def test_full_journey_phone_to_product(client, people, store):
    achari, owner = people["achari"]["h"], people["owner"]["h"]
    item = make_item(client, owner)

    scan = queue(client, achari, store, item_id=item["id"])
    assert scan["status"] == "uploaded" and scan["files"] == 10
    assert scan["source_bytes"] == 10_000

    waiting = client.get("/scans?status=uploaded", headers=owner).json()
    assert scan["id"] in [s["id"] for s in waiting]

    got = claim(client, owner, scan["id"])
    assert got.status_code == 200
    assert got.json()["status"] == "processing" and got.json()["worker"] == PC
    assert got.json()["attempts"] == 1

    r = client.post(
        f"/scans/{scan['id']}/progress",
        headers=owner,
        json={"worker": PC, "percent": 40, "note": "Reconstructing"},
    )
    assert r.json()["progress"] == 40 and r.json()["note"] == "Reconstructing"

    urls = client.get(
        f"/scans/{scan['id']}/download-urls", headers=owner, params={"worker": PC}
    ).json()
    assert len(urls) == 10
    assert all(u["url"].startswith("https://store.invalid/") for u in urls)

    stl = put_result(client, owner, store, scan["id"], "stl", "chair.stl")
    render = put_result(
        client, owner, store, scan["id"], "render", "front.png", png_bytes()
    )
    body = {
        "worker": PC,
        "assets": [
            {
                "key": stl,
                "kind": "stl",
                "visibility": "staff",
                "content_type": "model/stl",
            },
            {
                "key": render,
                "kind": "render",
                "visibility": "public",
                "content_type": "image/png",
            },
        ],
    }
    done = client.post(f"/scans/{scan['id']}/finish", headers=owner, json=body)
    assert done.status_code == 200, done.text
    assert done.json()["status"] == "done" and len(done.json()["result_asset_ids"]) == 2

    # idempotent: the same call again changes nothing
    again = client.post(f"/scans/{scan['id']}/finish", headers=owner, json=body)
    assert again.status_code == 200
    assert again.json()["result_asset_ids"] == done.json()["result_asset_ids"]

    # the website shows the published render and never the .stl
    pub = client.get(f"/public/catalog/{item['slug']}")
    assert len(pub.json()["renders"]) == 1
    assert "stl" not in pub.text and stl not in pub.text


# ---------------------------------------------------------------- capture rules
def test_capture_checks(client, people, store):
    h = people["achari"]["h"]
    scan, keys = capture(client, h, store, photos=10)

    store.objects.pop(keys[0])  # an object that never arrived
    r = client.post(f"/scans/{scan['id']}/complete", headers=h, json={"keys": keys})
    assert r.status_code == 409 and r.json()["detail"]["missing"] == [keys[0]]
    assert client.get(f"/scans/{scan['id']}", headers=h).json()["status"] == "created"

    store.objects[keys[0]] = b"x"
    r = client.post(f"/scans/{scan['id']}/complete", headers=h, json={"keys": keys[:3]})
    assert r.status_code == 422  # too few photos

    _, other_keys = capture(client, h, store, photos=10)  # keys from another scan
    r = client.post(
        f"/scans/{scan['id']}/complete", headers=h, json={"keys": other_keys}
    )
    assert r.status_code == 400

    for _ in range(2):  # a retried complete is harmless
        ok = client.post(
            f"/scans/{scan['id']}/complete", headers=h, json={"keys": keys}
        )
        assert ok.status_code == 200


def test_presign_rules(client, people, store):
    h = people["achari"]["h"]
    scan = client.post("/scans", headers=h, json={"source_kind": "video"}).json()

    def presign(files):
        return client.post(
            f"/scans/{scan['id']}/presign", headers=h, json={"files": files}
        )

    ok = {"filename": "a.mp4", "content_type": "video/mp4", "size": 5_000_000}
    assert presign([ok]).status_code == 200
    assert presign([ok, ok]).status_code == 422  # one video only
    assert presign([{**ok, "filename": "a.exe"}]).status_code == 415
    assert presign([{**ok, "size": 3 * 1024**3}]).status_code == 413
    assert presign([{**ok, "content_type": "no slash"}]).status_code == 400
    _, ctype, cap = store.signed[0]
    assert (
        ctype == "video/mp4" and cap == 2048 * 1024 * 1024
    )  # the store enforces the cap


def test_you_only_see_your_own_scans(client, people, store):
    a, owner, w = people["achari"]["h"], people["owner"]["h"], people["worker"]["h"]
    mine = queue(client, a, store)
    assert [s["id"] for s in client.get("/scans", headers=a).json()] == [mine["id"]]
    assert client.get(f"/scans/{mine['id']}", headers=owner).status_code == 200
    assert client.get("/scans", headers=w).json() == []
    assert client.get(f"/scans/{mine['id']}", headers=w).status_code == 404


# ---------------------------------------------------------------- the queue
def test_only_owner_or_manager_can_claim(client, people, store):
    scan = queue(client, people["achari"]["h"], store)
    for role in ("achari", "worker", "sales"):
        assert claim(client, people[role]["h"], scan["id"]).status_code == 403
    assert claim(client, people["manager"]["h"], scan["id"]).status_code == 200


def test_two_pcs_cannot_both_win(client, people, store):
    owner = people["owner"]["h"]
    scan = queue(client, owner, store)
    assert claim(client, owner, scan["id"], "pc-1").status_code == 200
    assert claim(client, owner, scan["id"], "pc-2").status_code == 409
    r = client.post(
        f"/scans/{scan['id']}/progress",
        headers=owner,
        json={"worker": "pc-2", "percent": 5},
    )
    assert r.status_code == 409  # pc-2 cannot report on pc-1's job


def test_a_dead_pc_releases_the_job_then_gives_up(client, db, people, store):
    owner = people["owner"]["h"]
    scan = queue(client, owner, store)

    def lapse():
        db.expire_all()
        job = db.get(ScanJob, uuid.UUID(scan["id"]))
        job.lease_until = datetime.now(timezone.utc) - timedelta(minutes=1)
        db.commit()

    for attempt in (1, 2, 3):
        r = claim(client, owner, scan["id"], f"pc-{attempt}")
        assert r.status_code == 200 and r.json()["attempts"] == attempt
        lapse()
    assert claim(client, owner, scan["id"], "pc-4").status_code == 409
    job = client.get(f"/scans/{scan['id']}", headers=owner).json()
    assert job["status"] == "failed" and "3 attempts" in job["error"]


def test_progress_renews_the_lease(client, people, store):
    owner = people["owner"]["h"]
    scan = queue(client, owner, store)
    first = claim(client, owner, scan["id"]).json()["lease_until"]
    r = client.post(
        f"/scans/{scan['id']}/progress",
        headers=owner,
        json={"worker": PC, "percent": 10},
    )
    assert r.json()["lease_until"] >= first


def test_fail_reports_a_reason_the_phone_can_see(client, people, store):
    a, owner = people["achari"]["h"], people["owner"]["h"]
    scan = queue(client, a, store)
    claim(client, owner, scan["id"])
    r = client.post(
        f"/scans/{scan['id']}/fail",
        headers=owner,
        json={"worker": "other", "error": "x"},
    )
    assert r.status_code == 409
    r = client.post(
        f"/scans/{scan['id']}/fail",
        headers=owner,
        json={
            "worker": PC,
            "error": "Not enough overlap between photos. Take more from each side.",
        },
    )
    assert r.json()["status"] == "failed"
    assert (
        "Not enough overlap"
        in client.get(f"/scans/{scan['id']}", headers=a).json()["error"]
    )


# ---------------------------------------------------------------- cancel
def test_cancel(client, people, store):
    a, owner = people["achari"]["h"], people["owner"]["h"]
    scan = queue(client, a, store)
    keys = list(store.objects)
    r = client.post(f"/scans/{scan['id']}/cancel", headers=a)
    assert r.json()["status"] == "cancelled"
    assert not any(k in store.objects for k in keys)
    assert claim(client, owner, scan["id"]).status_code == 409

    busy = queue(client, a, store)
    claim(client, owner, busy["id"])
    assert client.post(f"/scans/{busy['id']}/cancel", headers=a).status_code == 409


# ---------------------------------------------------------------- finishing safely
def test_finish_rules(client, people, store):
    owner = people["owner"]["h"]
    item = make_item(client, owner)
    scan = queue(client, owner, store)
    claim(client, owner, scan["id"])
    stl = put_result(client, owner, store, scan["id"], "stl", "a.stl")

    def finish(assets, **extra):
        return client.post(
            f"/scans/{scan['id']}/finish",
            headers=owner,
            json={"worker": PC, "assets": assets, **extra},
        )

    asset = {
        "key": stl,
        "kind": "stl",
        "visibility": "staff",
        "content_type": "model/stl",
    }
    assert finish([asset]).status_code == 422  # no product chosen yet
    public_stl = {**asset, "visibility": "public"}
    assert finish([public_stl], item_id=item["id"]).status_code == 400  # never public
    foreign = {**asset, "key": f"items/{item['id']}/stl/x.stl"}
    assert finish([foreign], item_id=item["id"]).status_code == 400
    ghost = f"scans/{scan['id']}/out/ghost.stl"
    r = finish([asset, {**asset, "key": ghost}], item_id=item["id"])
    assert r.status_code == 409 and r.json()["detail"]["missing"] == [ghost]
    assert (
        client.get(f"/scans/{scan['id']}", headers=owner).json()["status"]
        == "processing"
    )
    assert finish([asset], item_id=item["id"]).status_code == 200


def test_results_are_staff_only_by_default(client, people, store):
    owner = people["owner"]["h"]
    item = make_item(client, owner)
    scan = queue(client, owner, store, item_id=item["id"])
    claim(client, owner, scan["id"])
    nc = put_result(client, owner, store, scan["id"], "nc", "chair.nc")
    r = client.post(
        f"/scans/{scan['id']}/finish",
        headers=owner,
        json={
            "worker": PC,
            "assets": [{"key": nc, "kind": "nc", "content_type": "text/plain"}],
        },
    )
    assert r.status_code == 200, r.text
    pub = client.get(f"/public/catalog/{item['slug']}")
    assert nc not in pub.text and ".nc" not in pub.text
    assets = client.get(f"/items/{item['id']}/assets", headers=owner).json()
    assert assets and all(a["visibility"] == "staff" for a in assets)
