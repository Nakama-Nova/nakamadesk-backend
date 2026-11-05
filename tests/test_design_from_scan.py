"""On request: a finished flat-carving scan becomes a furniture design (picture, depth, G-code)."""

import io
import os

from PIL import Image

from app.models.product_asset import ProductAsset
from app.models.restoration import RestorationFile
from app.models.scan_job import ScanJob
from tests.test_lab_auth import open_session
from tests.test_restoration import new_project


def depth_png() -> bytes:
    """A small grey-scale depth picture; each call is a different one."""
    out = io.BytesIO()
    Image.new("L", (80, 50), os.urandom(1)[0]).save(out, "PNG")
    return out.getvalue() + os.urandom(
        4
    )  # trailing bytes keep it unique and still a valid PNG


def finished_scan(
    db,
    store,
    item_id=None,
    restoration_id=None,
    shape="relief",
    picture=None,
    gcode=False,
    created_by=None,
    status="done",
    width=600.0,
):
    """A scan the worker has finished: rows and files as /scans/{id}/finish leaves them."""
    job = ScanJob(
        item_id=item_id,
        restoration_id=restoration_id,
        artisan_only=bool(restoration_id),
        created_by=created_by,
        status=status,
        source_kind="photos",
        params={"shape": shape, "real_width_mm": width, "gcode": gcode},
        progress=100,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    picture = depth_png() if picture is None else picture
    files = [("render", "relief-depth.png", picture, "image/png")] if picture else []
    if gcode:
        files.append(("nc", "relief.nc", b"G21\nG0 Z5\nM2\n", "text/plain"))
    for kind, name, data, ctype in files:
        key = f"scans/{job.id}/out/{name}"
        store.put_bytes(key, data, ctype)
        if restoration_id:
            db.add(
                RestorationFile(
                    restoration_id=restoration_id,
                    scan_id=job.id,
                    kind=kind,
                    key=key,
                    content_type=ctype,
                    size_bytes=len(data),
                    label=name,
                )
            )
        else:
            db.add(
                ProductAsset(
                    item_id=item_id,
                    kind=kind,
                    storage_key=key,
                    content_type=ctype,
                    bytes=len(data),
                    storage="s3",
                    visibility="staff",
                    scan_id=job.id,
                    alt=name,
                )
            )
    db.commit()
    return job, picture


def make(client, people, job, role="owner", **body):
    return client.post(
        "/designs/from-scan",
        headers=people[role]["h"],
        json={"scan_id": str(job.id), **body},
    )


# ------------------------------------------------------------------ the design
def test_a_flat_carving_scan_becomes_a_furniture_design_on_request(
    client, people, chair, db, store
):
    job, picture = finished_scan(db, store, item_id=chair["id"], gcode=True)
    # finishing a scan alone makes nothing: it is on request
    h = people["owner"]["h"]
    assert (
        client.get("/designs", headers=h, params={"category": "furniture"}).json() == []
    )

    r = make(client, people, job)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["created"] is True and d["category"] == "furniture"
    assert d["name"] == "Dining Chair carving" and d["has_image"] is True
    assert d["scan_id"] == str(job.id)
    assert "600 mm wide" in d["description"]

    got = client.get(
        f"/designs/{d['id']}/image", headers=h
    )  # the depth picture is its picture
    assert got.status_code == 200 and got.content == picture

    detail = client.get(f"/designs/{d['id']}", headers=h).json()
    assert [p["id"] for p in detail["products"]] == [
        chair["id"]
    ]  # linked to the piece scanned
    files = {f["kind"]: f for f in detail["scan_files"]}
    assert set(files) == {"render", "nc"}  # the G-code to carve from is one click away
    assert (
        files["nc"]["download_path"]
        == f"/items/{chair['id']}/assets/{files['nc']['id']}/download"
    )
    assert (
        client.get("/designs", headers=h, params={"category": "furniture"}).json()[0][
            "id"
        ]
        == d["id"]
    )


def test_asking_again_gives_the_same_design_never_a_copy(
    client, people, chair, db, store
):
    first, picture = finished_scan(db, store, item_id=chair["id"])
    a = make(client, people, first).json()
    again = make(client, people, first).json()
    assert again["id"] == a["id"] and again["created"] is False
    # another scan whose picture is the very same file reuses that design too
    twin, _ = finished_scan(db, store, item_id=chair["id"], picture=picture)
    b = make(client, people, twin).json()
    assert b["id"] == a["id"] and b["created"] is False
    # a different picture is a new design with the next code
    other, _ = finished_scan(db, store, item_id=chair["id"])
    c = make(client, people, other, name="Lotus corner").json()
    assert c["created"] is True and c["id"] != a["id"] and c["name"] == "Lotus corner"
    assert c["sku"] != a["sku"]


def test_only_a_finished_flat_carving_with_a_picture_can_be_a_design(
    client, people, chair, db, store
):
    full, _ = finished_scan(db, store, item_id=chair["id"], shape="full")
    r = make(client, people, full)
    assert r.status_code == 422 and "flat-carving" in r.json()["detail"]

    running, _ = finished_scan(db, store, item_id=chair["id"], status="processing")
    assert make(client, people, running).status_code == 409

    bare, _ = finished_scan(db, store, item_id=chair["id"], picture=b"")
    r = make(client, people, bare)
    assert r.status_code == 409 and "no depth picture" in r.json()["detail"]

    gone, _ = finished_scan(db, store, item_id=chair["id"])
    store.objects.clear()  # the file vanished from the store
    assert make(client, people, gone).status_code == 409

    junk, _ = finished_scan(db, store, item_id=chair["id"], picture=b"not a picture")
    assert (
        make(client, people, junk).status_code == 400
    )  # same checks as any design picture

    ghost = type("S", (), {"id": "00000000-0000-0000-0000-000000000009"})()
    assert make(client, people, ghost).status_code == 404
    assert make(client, people, full, name="").status_code == 422


def test_who_may_make_a_design_from_a_scan(client, people, chair, db, store):
    job, _ = finished_scan(db, store, item_id=chair["id"])
    for role in ("manager", "sales", "achari", "worker"):
        assert make(client, people, job, role=role).status_code == 403, role
    assert (
        client.post("/designs/from-scan", json={"scan_id": str(job.id)}).status_code
        == 401
    )
    # an artisan sees only their own product scans, like everywhere else
    assert make(client, people, job, role="artisan").status_code == 404
    mine, _ = finished_scan(
        db, store, item_id=chair["id"], created_by=people["artisan"]["id"]
    )
    assert make(client, people, mine, role="artisan").status_code == 200


def test_a_restoration_scan_uses_the_projects_private_files(client, people, db, store):
    project = new_project(client, people["owner"]["h"])
    job, picture = finished_scan(db, store, restoration_id=project["id"], gcode=True)
    d = make(client, people, job, role="artisan").json()
    assert d["created"] is True and d["restoration_id"] == project["id"]
    assert d["name"] == f"{project['title']} carving"
    h = people["owner"]["h"]
    assert client.get(f"/designs/{d['id']}/image", headers=h).content == picture
    kinds = {
        f["kind"] for f in client.get(f"/designs/{d['id']}", headers=h).json()["files"]
    }
    assert kinds == {
        "render",
        "nc",
    }  # the existing restoration files list already shows them


# ------------------------------------------------------------------ from the Lab editor
def test_the_editor_lists_the_scans_and_the_owner_makes_a_design_then_carves_it(
    client, people, chair, db, store
):
    job, _ = finished_scan(db, store, item_id=chair["id"], gcode=True)
    finished_scan(
        db, store, item_id=chair["id"], shape="full"
    )  # not a flat carving: not listed
    finished_scan(db, store, item_id=chair["id"], picture=b"")  # no picture: not listed
    h = people["owner"]["h"]
    url = f"/lab/items/{chair['id']}/definition"
    scans = client.get(url, headers=h).json()["scans"]
    assert [(s["id"], s["design"], s["real_width_mm"], s["gcode"]) for s in scans] == [
        (str(job.id), None, 600.0, True)
    ]

    r = client.post(
        f"/lab/scans/{job.id}/design", headers=h, json={"name": "Chair back lotus"}
    )
    assert r.status_code == 200, r.text
    made = r.json()
    assert made["created"] is True and made["design"]["name"] == "Chair back lotus"
    assert (
        made["design"]["category"] == "furniture"
        and made["design"]["has_image"] is True
    )

    after = client.get(url, headers=h).json()
    assert after["scans"][0]["design"]["sku"] == made["design"]["sku"]
    assert made["design"]["id"] in {
        d["id"] for d in after["designs"]
    }  # ready to pick in Carvings

    seat = next(p for p in after["parts"] if p["name"] == "Seat")
    carve = {
        "part_id": seat["id"],
        "design_id": made["design"]["id"],
        "face": "top",
        "margin_mm": 20,
    }
    r = client.put(
        f"/lab/items/{chair['id']}/carvings", headers=h, json={"carvings": [carve]}
    )
    assert r.status_code == 200, r.text
    assert r.json()["designs"][0]["sku"] == made["design"]["sku"]
    # the depth picture now shows in the Lab, and the design lists where it is carved
    assert (
        client.get(
            r.json()["designs"][0]["image_url"], headers=people["worker"]["h"]
        ).status_code
        == 200
    )
    use = client.get(f"/designs/{made['design']['id']}", headers=h).json()["furniture"]
    assert [(u["item_name"], u["part_name"], u["face"]) for u in use] == [
        ("Dining Chair", "Seat", "top")
    ]


def test_in_the_lab_only_the_owner_makes_designs_and_cookie_writes_need_the_header(
    client, people, chair, db, store
):
    job, _ = finished_scan(db, store, item_id=chair["id"])
    url = f"/lab/scans/{job.id}/design"
    for role in ("manager", "achari", "worker", "artisan", "sales"):
        assert (
            client.post(url, headers=people[role]["h"], json={}).status_code == 403
        ), role
    assert client.post(url, json={}).status_code == 401
    open_session(client, people, chair, role="owner", mode="edit")
    assert (
        client.post(url, json={}).status_code == 403
    )  # a cookie write without the Lab header
    assert client.post(url, json={}, headers={"X-Lab-Client": "1"}).status_code == 200
    # a scan that belongs to no piece cannot be used from the Lab
    loose, _ = finished_scan(db, store, picture=b"")
    assert (
        client.post(
            f"/lab/scans/{loose.id}/design", headers=people["owner"]["h"], json={}
        ).status_code
        == 404
    )
