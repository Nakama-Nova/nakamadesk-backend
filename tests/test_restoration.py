"""
Temple restoration: the artisan role, projects, scans on a project, the workshop PC
finishing them, and the rule that only the owner and artisans can see any of it.
"""

import uuid

import pytest

from tests.test_scans import PC, claim, make_item, put_result, queue

MB = 1024 * 1024
OUTSIDERS = ("manager", "sales", "achari", "worker")


def new_project(client, h, **extra):
    body = {
        "title": f"Door panel {uuid.uuid4().hex[:4]}",
        "temple_name": "Sri Veerabhadra Temple",
        "location": "Lepakshi",
        "piece": "Dwarapalaka, left",
        "condition_notes": "Nose and left hand missing",
        **extra,
    }
    r = client.post("/restorations", headers=h, json=body)
    assert r.status_code == 201, r.text
    return r.json()


def restoration_scan(client, h, store, project, **kw):
    scan = client.post(
        "/scans",
        headers=h,
        json={"restoration_id": project["id"], "source_kind": "photos"},
    )
    assert scan.status_code == 201, scan.text
    return scan.json()


def queue_restoration(client, h, store, project, photos=10):
    scan = restoration_scan(client, h, store, project)
    presign = client.post(
        f"/scans/{scan['id']}/presign",
        headers=h,
        json={"files": [{"filename": f"{i}.jpg", "content_type": "image/jpeg", "size": 1000} for i in range(photos)]},
    )  # fmt: skip
    assert presign.status_code == 200, presign.text
    keys = [f["key"] for f in presign.json()["files"]]
    for k in keys:
        store.objects[k] = b"x" * 1000
    done = client.post(f"/scans/{scan['id']}/complete", headers=h, json={"keys": keys})
    assert done.status_code == 200, done.text
    return done.json()


# ----------------------------------------------------------------- the role
def test_the_owner_can_create_an_artisan_account(client, people):
    name = f"carver_{uuid.uuid4().hex[:6]}"
    r = client.post(
        "/auth/register",
        headers=people["owner"]["h"],
        json={
            "username": name,
            "email": f"{name}@example.com",
            "password": "StartPass123",
            "role": "artisan",
            "base_daily_wage": 900,
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["role"] == "artisan"
    login = client.post(
        "/auth/login", data={"username": name, "password": "StartPass123"}
    )
    assert login.status_code == 200 and login.json()["role"] == "artisan"


def test_an_artisan_can_read_stock_and_the_lab_but_not_money(client, people):
    h = people["artisan"]["h"]
    assert client.get("/items/", headers=h).status_code == 200
    assert client.get("/lab/items", headers=h).status_code == 200
    assert client.get("/reports/profit", headers=h).status_code == 403
    assert client.get("/expenses/", headers=h).status_code == 403
    assert client.get("/payroll/me", headers=h).status_code == 200  # own pay only


# ----------------------------------------------------------------- projects
def test_owner_and_artisan_run_a_project(client, people):
    owner, artisan = people["owner"]["h"], people["artisan"]["h"]
    p = new_project(client, artisan, lead_artisan_id=people["artisan"]["id"])
    assert p["status"] == "planned" and p["scan_count"] == 0 and p["file_count"] == 0
    assert p["lead_artisan_name"]

    listed = client.get("/restorations", headers=owner).json()
    assert p["id"] in [x["id"] for x in listed]
    only = client.get("/restorations?status=carving", headers=owner).json()
    assert p["id"] not in [x["id"] for x in only]

    r = client.patch(
        f"/restorations/{p['id']}",
        headers=owner,
        json={"status": "carving", "condition_notes": "Hand recarved"},
    )
    assert r.status_code == 200 and r.json()["status"] == "carving"
    assert (
        client.get(f"/restorations/{p['id']}", headers=artisan).json()[
            "condition_notes"
        ]
        == "Hand recarved"
    )


def test_project_validation(client, people):
    owner = people["owner"]["h"]
    p = new_project(client, owner)
    base = {"title": "x", "temple_name": "T"}
    assert (
        client.post(
            "/restorations", headers=owner, json={**base, "title": ""}
        ).status_code
        == 422
    )
    for who in ("worker", "manager", "achari"):  # only the owner or an artisan may lead
        r = client.post(
            "/restorations",
            headers=owner,
            json={**base, "lead_artisan_id": people[who]["id"]},
        )
        assert r.status_code == 422, who
    bad = client.patch(
        f"/restorations/{p['id']}", headers=owner, json={"status": "finished"}
    )
    assert bad.status_code == 422
    assert (
        client.patch(
            f"/restorations/{p['id']}", headers=owner, json={"title": None}
        ).status_code
        == 422
    )
    assert client.get(f"/restorations/{uuid.uuid4()}", headers=owner).status_code == 404
    team = client.get("/restorations/team", headers=owner).json()
    assert {t["role"] for t in team} <= {"owner", "artisan"} and team


@pytest.mark.parametrize("role", OUTSIDERS)
def test_nobody_else_can_see_or_touch_restorations(client, people, store, role):
    owner, h = people["owner"]["h"], people[role]["h"]
    p = new_project(client, owner)
    queue_restoration(client, owner, store, p)
    assert client.get("/restorations", headers=h).status_code == 403
    assert client.get("/restorations/team", headers=h).status_code == 403
    assert client.get(f"/restorations/{p['id']}", headers=h).status_code == 403
    assert (
        client.post(
            "/restorations", headers=h, json={"title": "x", "temple_name": "T"}
        ).status_code
        == 403
    )
    assert (
        client.patch(
            f"/restorations/{p['id']}", headers=h, json={"status": "restored"}
        ).status_code
        == 403
    )
    # and they cannot start a scan on one
    r = client.post(
        "/scans", headers=h, json={"restoration_id": p["id"], "source_kind": "photos"}
    )
    assert r.status_code == 403
    assert client.get("/restorations").status_code == 401


# ------------------------------------------------------------------- scans
def test_a_scan_is_for_a_product_or_a_restoration_not_both(client, people, store):
    owner = people["owner"]["h"]
    p = new_project(client, owner)
    item = make_item(client, owner)
    r = client.post(
        "/scans",
        headers=owner,
        json={
            "restoration_id": p["id"],
            "item_id": item["id"],
            "source_kind": "photos",
        },
    )
    assert r.status_code == 422
    missing = client.post(
        "/scans",
        headers=owner,
        json={"restoration_id": str(uuid.uuid4()), "source_kind": "photos"},
    )
    assert missing.status_code == 404


def test_uploading_a_scan_moves_the_project_on(client, people, store):
    artisan = people["artisan"]["h"]
    p = new_project(client, artisan)
    scan = queue_restoration(client, artisan, store, p)
    assert scan["restoration_id"] == p["id"] and scan["status"] == "uploaded"
    detail = client.get(f"/restorations/{p['id']}", headers=artisan).json()
    assert detail["status"] == "processing" and detail["scan_count"] == 1
    assert [s["id"] for s in detail["scans"]] == [scan["id"]]


def test_restoration_scans_are_hidden_from_everyone_else(client, people, store):
    owner, artisan, manager = (
        people["owner"]["h"],
        people["artisan"]["h"],
        people["manager"]["h"],
    )
    p = new_project(client, artisan)
    scan = queue_restoration(client, artisan, store, p)
    item_scan = queue(
        client, people["achari"]["h"], store, item_id=make_item(client, owner)["id"]
    )

    # the manager runs the product queue but never sees a restoration job
    queue_ids = [
        s["id"] for s in client.get("/scans?status=uploaded", headers=manager).json()
    ]
    assert item_scan["id"] in queue_ids and scan["id"] not in queue_ids
    assert client.get(f"/scans/{scan['id']}", headers=manager).status_code == 404
    assert claim(client, manager, scan["id"]).status_code == 404
    for role in ("achari", "worker", "sales"):
        assert scan["id"] not in [
            s["id"] for s in client.get("/scans", headers=people[role]["h"]).json()
        ]
        assert (
            client.get(f"/scans/{scan['id']}", headers=people[role]["h"]).status_code
            == 404
        )

    # owner and any artisan see it, and see it together
    for h in (owner, artisan):
        assert scan["id"] in [
            s["id"] for s in client.get("/scans?status=uploaded", headers=h).json()
        ]
    other_artisan = people["artisan"]["h"]
    assert (
        client.get(f"/scans?restoration_id={p['id']}", headers=other_artisan).json()[0][
            "id"
        ]
        == scan["id"]
    )

    # an artisan still cannot run a PRODUCT scan on the PC
    assert claim(client, artisan, item_scan["id"]).status_code == 404
    assert item_scan["id"] not in [
        s["id"] for s in client.get("/scans", headers=artisan).json()
    ]


# ----------------------------------------------------- the workshop PC's journey
def test_the_workshop_pc_finishes_a_restoration_into_private_files(
    client, people, store
):
    owner, artisan = people["owner"]["h"], people["artisan"]["h"]
    p = new_project(client, artisan)
    scan = queue_restoration(client, artisan, store, p)

    got = claim(
        client, owner, scan["id"]
    )  # the PC signs in as the owner (or an artisan)
    assert got.status_code == 200 and got.json()["status"] == "processing"
    urls = client.get(
        f"/scans/{scan['id']}/download-urls", headers=owner, params={"worker": PC}
    )
    assert urls.status_code == 200 and len(urls.json()) == 10

    glb = put_result(
        client, owner, store, scan["id"], "glb", "model.glb", b"glTF" + b"x" * 20
    )
    stl = put_result(client, owner, store, scan["id"], "stl", "model.stl", b"solid x")
    nc = put_result(client, owner, store, scan["id"], "nc", "panel.nc", b"G21")
    assets = [
        {
            "key": glb,
            "kind": "glb",
            "content_type": "model/gltf-binary",
            "alt": "3D model",
        },
        {"key": stl, "kind": "stl", "content_type": "model/stl"},
        {"key": nc, "kind": "nc", "content_type": "text/plain"},
    ]

    # a restoration result can never be published
    pub = client.post(
        f"/scans/{scan['id']}/finish",
        headers=owner,
        json={"worker": PC, "assets": [{**assets[0], "visibility": "public"}]},
    )
    assert pub.status_code == 400
    done = client.post(
        f"/scans/{scan['id']}/finish",
        headers=owner,
        json={"worker": PC, "assets": assets},
    )
    assert done.status_code == 200, done.text
    assert done.json()["status"] == "done" and len(done.json()["result_asset_ids"]) == 3

    detail = client.get(f"/restorations/{p['id']}", headers=artisan).json()
    assert detail["status"] == "review" and detail["file_count"] == 3
    assert {f["kind"] for f in detail["files"]} == {"glb", "stl", "nc"}

    f = next(x for x in detail["files"] if x["kind"] == "glb")
    link = client.get(
        f"/restorations/{p['id']}/files/{f['id']}/download", headers=artisan
    ).json()
    assert link["url"].startswith("https://store.invalid/") and link[
        "filename"
    ].endswith("-glb.glb")
    assert (
        client.get(
            f"/restorations/{p['id']}/files/{f['id']}/download",
            headers=people["manager"]["h"],
        ).status_code
        == 403
    )
    assert (
        client.get(
            f"/restorations/{uuid.uuid4()}/files/{f['id']}/download", headers=artisan
        ).status_code
        == 404
    )


def test_results_never_reach_the_website_or_the_product_catalogue(
    client, people, store, db
):
    from app.models.product_asset import ProductAsset

    owner = people["owner"]["h"]
    before = db.query(ProductAsset).count()
    p = new_project(client, owner)
    scan = queue_restoration(client, owner, store, p)
    claim(client, owner, scan["id"])
    glb = put_result(
        client, owner, store, scan["id"], "glb", "m.glb", b"glTF" + b"x" * 8
    )
    done = client.post(
        f"/scans/{scan['id']}/finish",
        headers=owner,
        json={
            "worker": PC,
            "assets": [
                {"key": glb, "kind": "glb", "content_type": "model/gltf-binary"}
            ],
        },
    )
    assert done.status_code == 200
    assert db.query(ProductAsset).count() == before  # not a product asset
    catalog = client.get("/public/catalog")
    assert glb not in catalog.text and p["title"] not in catalog.text


def test_an_artisan_account_can_be_the_workshop_pc(client, people, store):
    artisan = people["artisan"]["h"]
    p = new_project(client, artisan)
    scan = queue_restoration(client, artisan, store, p)
    assert claim(client, artisan, scan["id"]).status_code == 200
    assert (
        client.post(
            f"/scans/{scan['id']}/fail",
            headers=artisan,
            json={"worker": PC, "error": "COLMAP found no match"},
        ).status_code
        == 200
    )
    assert (
        client.get(f"/scans/{scan['id']}", headers=artisan).json()["error"]
        == "COLMAP found no match"
    )


# ------------------------------------------------------------------ the limits
def test_capture_size_limits_are_100mb_a_photo_and_1gb_a_video(client, people, store):
    h = people["artisan"]["h"]
    p = new_project(client, h)

    def presign(kind, filename, ctype, size):
        scan = client.post(
            "/scans", headers=h, json={"restoration_id": p["id"], "source_kind": kind}
        ).json()
        return client.post(
            f"/scans/{scan['id']}/presign",
            headers=h,
            json={
                "files": [{"filename": filename, "content_type": ctype, "size": size}]
            },
        )

    assert presign("photos", "a.jpg", "image/jpeg", 100 * MB).status_code == 200
    assert presign("photos", "a.jpg", "image/jpeg", 100 * MB + 1).status_code == 413
    assert presign("video", "a.mp4", "video/mp4", 1024 * MB).status_code == 200
    assert presign("video", "a.mp4", "video/mp4", 1024 * MB + 1).status_code == 413
    assert (
        "100 MB" in presign("photos", "a.jpg", "image/jpeg", 200 * MB).json()["detail"]
    )


# --------------------------------------------- a bad ending does not leave "processing"
def test_a_failed_scan_puts_the_project_back_to_capturing(client, people, store):
    owner = people["owner"]["h"]
    p = new_project(client, owner)
    scan = queue_restoration(client, owner, store, p)
    assert (
        client.get(f"/restorations/{p['id']}", headers=owner).json()["status"]
        == "processing"
    )
    assert claim(client, owner, scan["id"]).status_code == 200
    r = client.post(
        f"/scans/{scan['id']}/fail",
        headers=owner,
        json={"worker": PC, "error": "COLMAP was not found"},
    )
    assert r.status_code == 200
    assert (
        client.get(f"/restorations/{p['id']}", headers=owner).json()["status"]
        == "capturing"
    )


def test_another_active_scan_keeps_the_project_processing(client, people, store):
    owner = people["owner"]["h"]
    p = new_project(client, owner)
    first, second = queue_restoration(client, owner, store, p), queue_restoration(
        client, owner, store, p
    )
    claim(client, owner, first["id"])
    client.post(
        f"/scans/{first['id']}/fail",
        headers=owner,
        json={"worker": PC, "error": "bad photos"},
    )
    assert (
        client.get(f"/restorations/{p['id']}", headers=owner).json()["status"]
        == "processing"
    )  # second is waiting
    assert (
        client.post(f"/scans/{second['id']}/cancel", headers=owner).status_code == 200
    )
    assert (
        client.get(f"/restorations/{p['id']}", headers=owner).json()["status"]
        == "capturing"
    )


def test_a_failure_never_overrides_a_status_the_artisans_chose(client, people, store):
    owner = people["owner"]["h"]
    p = new_project(client, owner)
    scan = queue_restoration(client, owner, store, p)
    client.patch(f"/restorations/{p['id']}", headers=owner, json={"status": "carving"})
    claim(client, owner, scan["id"])
    client.post(
        f"/scans/{scan['id']}/fail", headers=owner, json={"worker": PC, "error": "x"}
    )
    assert (
        client.get(f"/restorations/{p['id']}", headers=owner).json()["status"]
        == "carving"
    )
