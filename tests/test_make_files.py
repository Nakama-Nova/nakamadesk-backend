"""Files made on request from a flat carving's stored depth map, not stored up front."""

from app.models.product_asset import ProductAsset
from app.models.scan_job import ScanJob
from tests.test_design_from_scan import finished_scan


def with_master(db, store, item_id, **kw):
    """A finished scan that also kept its depth map (what the worker leaves now)."""
    job, _ = finished_scan(db, store, item_id=item_id, **kw)
    data = b"\x89PNG-depth-master"
    key = f"scans/{job.id}/out/depth.png"
    store.put_bytes(key, data, "image/png")
    db.add(
        ProductAsset(
            item_id=item_id,
            kind="depth",
            storage_key=key,
            content_type="image/png",
            bytes=len(data),
            storage="s3",
            visibility="staff",
            scan_id=job.id,
            alt="Depth map (master)",
        )
    )
    db.commit()
    return job, key


def ask(client, people, job, role="owner", **body):
    body.setdefault("outputs", ["stl", "nc"])
    return client.post(
        f"/lab/scans/{job.id}/make-files", headers=people[role]["h"], json=body
    )


def test_asking_for_files_queues_a_job_for_the_workshop_pc_from_the_depth_map(
    client, people, chair, db, store
):
    job, key = with_master(db, store, chair["id"], width=1471.0)
    r = ask(client, people, job)
    assert r.status_code == 201, r.text
    queued = db.get(ScanJob, r.json()["id"])
    assert queued.status == "uploaded" and queued.item_id == job.item_id
    assert queued.source_keys == [key]  # the depth map is its only input
    assert queued.params == {
        "shape": "export",
        "from_scan": str(job.id),
        "outputs": ["nc", "stl"],
        "real_width_mm": 1471.0,
        "label": None,
        "gcode": True,
    }
    # asking for the very same thing twice queues one job
    assert ask(client, people, job).json()["id"] == r.json()["id"]
    # another width is a different request
    other = ask(client, people, job, real_width_mm=1200)
    assert other.status_code == 201 and other.json()["id"] != r.json()["id"]


def test_the_scan_list_shows_what_is_kept_and_what_was_asked_for(
    client, people, chair, db, store
):
    job, _ = with_master(db, store, chair["id"])
    queued = ask(client, people, job).json()
    rows = client.get(
        f"/lab/items/{chair['id']}/scans", headers=people["owner"]["h"]
    ).json()
    row = next(r for r in rows if r["id"] == str(job.id))
    assert row["can_make_files"] is True
    assert {f["kind"] for f in row["files"]} == {"render", "depth"}
    assert [e["id"] for e in row["exports"]] == [queued["id"]]
    assert row["exports"][0]["status"] == "uploaded" and row["exports"][0][
        "outputs"
    ] == ["nc", "stl"]
    assert all(
        r["id"] != queued["id"] for r in rows
    )  # a files request is not a scan to make a design from


def test_a_scan_without_a_depth_map_or_a_width_cannot_make_files(
    client, people, chair, db, store
):
    old, _ = finished_scan(
        db, store, item_id=chair["id"]
    )  # made before the depth map was kept
    r = ask(client, people, old)
    assert r.status_code == 422 and "Mark the area again" in r.json()["detail"]
    row = next(
        x
        for x in client.get(
            f"/lab/items/{chair['id']}/scans", headers=people["owner"]["h"]
        ).json()
        if x["id"] == str(old.id)
    )
    assert row["can_make_files"] is False
    nowidth, _ = with_master(db, store, chair["id"], width=None)
    r = ask(client, people, nowidth)
    assert r.status_code == 422 and "real width" in r.json()["detail"]
    assert ask(client, people, nowidth, real_width_mm=900).status_code == 201


def test_only_finished_flat_carvings_and_only_editors(client, people, chair, db, store):
    waiting, _ = with_master(db, store, chair["id"], status="uploaded")
    assert ask(client, people, waiting).status_code == 404
    full, _ = with_master(db, store, chair["id"], shape="full")
    assert ask(client, people, full).status_code == 404
    done, _ = with_master(db, store, chair["id"])
    assert ask(client, people, done, role="worker").status_code == 403
    assert ask(client, people, done, role="manager").status_code == 201
    assert ask(client, people, done, outputs=[]).status_code == 422
    assert ask(client, people, done, outputs=["dxf"]).status_code == 422


def test_the_worker_may_keep_a_depth_map_as_a_result(client, people, chair, db, store):
    job, _ = finished_scan(db, store, item_id=chair["id"], status="processing")
    job.claimed_by = f"{people['owner']['id']}|workshop-pc"
    db.commit()
    r = client.post(
        f"/scans/{job.id}/results/presign",
        headers=people["owner"]["h"],
        json={
            "worker": "workshop-pc",
            "kind": "depth",
            "filename": "depth.png",
            "content_type": "image/png",
            "size": 1000,
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["key"].startswith(f"scans/{job.id}/out/") and r.json()[
        "key"
    ].endswith(".png")


def test_a_scans_files_download_through_the_lab_page_and_sculptures_stay_private(
    client, people, chair, db, store
):
    job, _ = with_master(db, store, chair["id"])
    rows = client.get(
        f"/lab/items/{chair['id']}/scans", headers=people["owner"]["h"]
    ).json()
    row = next(r for r in rows if r["id"] == str(job.id))
    path = next(f["download_path"] for f in row["files"] if f["kind"] == "depth")
    assert path.startswith("/lab/assets/")
    r = client.get(path, headers=people["owner"]["h"], follow_redirects=False)
    assert r.status_code in (302, 307) and "depth.png" in r.headers["location"]
    assert (
        client.get(
            path, headers=people["worker"]["h"], follow_redirects=False
        ).status_code
        == 403
    )
    assert (
        client.get(
            "/lab/assets/00000000-0000-0000-0000-000000000000/download",
            headers=people["owner"]["h"],
        ).status_code
        == 404
    )


def test_a_cad_folder_can_be_asked_for_uploaded_by_the_worker_and_downloaded_as_a_zip(
    client, people, chair, db, store
):
    job, _ = with_master(db, store, chair["id"], width=1471.0)
    r = ask(client, people, job, outputs=["cad"])
    assert r.status_code == 201, r.text
    queued = db.get(ScanJob, r.json()["id"])
    assert queued.params["outputs"] == ["cad"] and queued.params["gcode"] is False

    # the workshop PC claims it, uploads the zip and finishes
    queued.status, queued.claimed_by = (
        "processing",
        f"{people['owner']['id']}|workshop-pc",
    )
    db.commit()
    p = client.post(
        f"/scans/{queued.id}/results/presign",
        headers=people["owner"]["h"],
        json={
            "worker": "workshop-pc",
            "kind": "cad",
            "filename": "cad-folder.zip",
            "content_type": "application/zip",
            "size": 5000,
        },
    )
    assert p.status_code == 200, p.text
    key = p.json()["key"]
    store.objects[key] = b"PK-zip"
    # a zip may not be passed off as another kind of file
    bad = client.post(
        f"/scans/{queued.id}/results/presign",
        headers=people["owner"]["h"],
        json={
            "worker": "workshop-pc",
            "kind": "stl",
            "filename": "x.zip",
            "content_type": "application/zip",
            "size": 10,
        },
    )
    assert bad.status_code == 415
    done = client.post(
        f"/scans/{queued.id}/finish",
        headers=people["owner"]["h"],
        json={
            "worker": "workshop-pc",
            "assets": [
                {
                    "key": key,
                    "kind": "cad",
                    "visibility": "staff",
                    "content_type": "application/zip",
                    "alt": "Queen bed (CAD folder)",
                }
            ],
        },
    )
    assert done.status_code == 200, done.text
    rows = client.get(
        f"/lab/items/{chair['id']}/scans", headers=people["owner"]["h"]
    ).json()
    export = next(r for r in rows if r["id"] == str(job.id))["exports"][0]
    assert export["status"] == "done" and export["files"][0]["kind"] == "cad"
    names = []
    real_get = store.presign_get
    store.presign_get = lambda key, expires=900, filename=None: (
        names.append(filename) or real_get(key, expires, filename)
    )
    r = client.get(
        export["files"][0]["download_path"],
        headers=people["owner"]["h"],
        follow_redirects=False,
    )
    assert r.status_code in (302, 307) and names == ["cad-folder.zip"]
