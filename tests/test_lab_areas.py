"""3D Lab: mark a carved area on a photo, send it to the workshop PC, get a design back."""

import io
import json

import pytest
from PIL import Image

from app.models.scan_job import ScanJob
from tests.test_design_from_scan import depth_png
from tests.test_lab_auth import open_session
from tests.test_scans import PC, claim, put_result

QUAD = [
    [100, 80],
    [540, 90],
    [530, 400],
    [110, 390],
]  # clockwise from the top-left of a 640 x 480 photo


def photo(kind="JPEG", size=(640, 480), orientation=None) -> bytes:
    out = io.BytesIO()
    img = Image.new("RGB", size, (190, 140, 90))
    if orientation:
        exif = img.getexif()
        exif[274] = orientation  # how the phone was held
        img.save(out, kind, exif=exif)
    else:
        img.save(out, kind)
    return out.getvalue()


def send(client, people, item, role="owner", data=None, ctype="image/jpeg", **fields):
    form = {
        "quad": json.dumps(QUAD),
        "label": "Headboard lotus",
        "real_width_mm": "600",
        **fields,
    }
    form = {k: v for k, v in form.items() if v is not None}
    return client.post(
        f"/lab/items/{item['id']}/areas",
        headers=people[role]["h"],
        files={"photo": ("bed.jpg", photo() if data is None else data, ctype)},
        data=form,
    )


def scans_of(client, people, item):
    r = client.get(f"/lab/items/{item['id']}/scans", headers=people["owner"]["h"])
    assert r.status_code == 200, r.text
    return r.json()


# ------------------------------------------------------------------ sending an area
def test_a_marked_area_is_queued_for_the_workshop_pc(client, people, chair, store, db):
    r = send(
        client, people, chair, outline=json.dumps([[120, 100], [500, 110], [300, 380]])
    )
    assert r.status_code == 201, r.text
    assert r.json()["status"] == "uploaded"
    job = db.query(ScanJob).filter(ScanJob.id == r.json()["id"]).one()
    assert (
        job.status == "uploaded"
        and job.item_id is not None
        and job.source_kind == "photos"
    )
    assert job.params["shape"] == "relief" and job.params["real_width_mm"] == 600
    assert job.params["label"] == "Headboard lotus"
    assert (
        job.params["crop"]["quad"] == QUAD and len(job.params["crop"]["outline"]) == 3
    )
    key = job.source_keys[0]
    assert key.startswith(f"scans/{job.id}/src/") and key in store.objects
    assert job.source_bytes == len(store.objects[key])

    listed = scans_of(client, people, chair)
    assert [(s["id"], s["status"], s["area"], s["label"]) for s in listed] == [
        (str(job.id), "uploaded", True, "Headboard lotus")
    ]
    defn = client.get(
        f"/lab/items/{chair['id']}/definition", headers=people["owner"]["h"]
    ).json()
    assert defn["scans"][0]["id"] == str(
        job.id
    )  # the editor page gets it with the rest


def test_the_whole_journey_area_worker_design_carving(client, people, chair, store):
    """Mark an area, a worker claims and finishes it, the owner makes a design from it."""
    owner = people["owner"]["h"]
    area = send(client, people, chair).json()
    assert claim(client, owner, area["id"]).status_code == 200
    got = client.get(f"/scans/{area['id']}", headers=owner).json()
    assert got["params"]["crop"]["quad"] == QUAD  # what the worker reads
    urls = client.get(
        f"/scans/{area['id']}/download-urls", headers=owner, params={"worker": PC}
    )
    assert urls.status_code == 200 and len(urls.json()) == 1

    picture = depth_png()
    key = put_result(
        client, owner, store, area["id"], "render", "relief-depth.png", picture
    )
    fin = client.post(
        f"/scans/{area['id']}/finish",
        headers=owner,
        json={
            "worker": PC,
            "assets": [
                {
                    "key": key,
                    "kind": "render",
                    "content_type": "image/png",
                    "alt": "depth",
                }
            ],
        },
    )
    assert fin.status_code == 200, fin.text
    assert [(s["status"], s["design"]) for s in scans_of(client, people, chair)] == [
        ("done", None)
    ]

    made = client.post(f"/lab/scans/{area['id']}/design", headers=owner, json={})
    assert made.status_code == 200, made.text
    assert (
        made.json()["design"]["name"] == "Headboard lotus"
    )  # the label becomes the design's name
    assert (
        client.get(
            f"/designs/{made.json()['design']['id']}/image", headers=owner
        ).content
        == picture
    )
    assert (
        scans_of(client, people, chair)[0]["design"]["sku"]
        == made.json()["design"]["sku"]
    )


def test_a_failed_area_shows_why(client, people, chair, store, db):
    area = send(client, people, chair).json()
    job = db.query(ScanJob).filter(ScanJob.id == area["id"]).one()
    job.status, job.error = "failed", "gave up after 3 attempts"
    db.commit()
    s = scans_of(client, people, chair)[0]
    assert s["status"] == "failed" and s["error"] == "gave up after 3 attempts"


# ------------------------------------------------------------------ checks
@pytest.mark.parametrize(
    "quad,expect",
    [
        ([[100, 80], [540, 90], [530, 400]], "four corners"),  # three corners
        (
            [[100, 80], [540, 90], [530, 400], [110, 700]],
            "outside the photo",
        ),  # below a 480 px photo
        (
            [[100, 80], [110, 390], [530, 400], [540, 90]],
            "clockwise",
        ),  # counter-clockwise
        ([[100, 80], [530, 400], [540, 90], [110, 390]], "clockwise"),  # crossing
        ([[100, 80], [110, 80], [110, 90], [100, 90]], "too small"),
    ],
)
def test_bad_corners_are_refused_with_the_reason(
    client, people, chair, store, quad, expect
):
    r = send(client, people, chair, quad=json.dumps(quad))
    assert r.status_code == 422 and expect in r.json()["detail"], r.text
    assert scans_of(client, people, chair) == []  # nothing was queued


def test_bad_outlines_and_other_fields_are_refused(client, people, chair, store):
    assert send(client, people, chair, quad="not json").status_code == 422
    assert (
        send(client, people, chair, outline=json.dumps([[1, 1], [5, 5]])).status_code
        == 422
    )
    r = send(client, people, chair, outline=json.dumps([[1, 1], [5, 5], [999, 5]]))
    assert r.status_code == 422 and "outside the photo" in r.json()["detail"]
    assert send(client, people, chair, real_width_mm="0").status_code == 422
    assert send(client, people, chair, real_height_mm="9999").status_code == 422
    assert send(client, people, chair, label="x" * 81).status_code == 422
    assert scans_of(client, people, chair) == []


def test_only_real_jpg_or_png_photos_up_to_the_size_limit(
    client, people, chair, store, monkeypatch
):
    assert (
        send(client, people, chair, data=photo("PNG"), ctype="image/png").status_code
        == 201
    )
    assert (
        send(client, people, chair, data=photo("WEBP"), ctype="image/webp").status_code
        == 415
    )
    assert send(client, people, chair, data=b"not a picture").status_code == 400
    assert send(client, people, chair, data=b"x", ctype="text/plain").status_code == 415
    monkeypatch.setattr("app.api.lab.AREA_PHOTO_MAX", 1000)
    assert send(client, people, chair).status_code == 413


def test_corners_are_in_the_pixels_of_the_photo_as_it_looks(
    client, people, chair, store
):
    """A phone photo stored sideways: the screen shows it turned, so 480 x 640, not 640 x 480."""
    tall = [
        [100, 100],
        [400, 110],
        [390, 600],
        [110, 590],
    ]  # y up to 600: only fits once turned
    turned = photo(orientation=6)
    assert (
        send(client, people, chair, data=turned, quad=json.dumps(tall)).status_code
        == 201
    )
    assert (
        send(client, people, chair, quad=json.dumps(tall)).status_code == 422
    )  # a normal 640 x 480 photo


# ------------------------------------------------------------------ who and where
def test_who_may_send_an_area(client, people, chair, store):
    for role in ("achari", "worker", "artisan", "sales"):
        assert send(client, people, chair, role=role).status_code == 403, role
    r = client.post(
        f"/lab/items/{chair['id']}/areas",
        files={"photo": ("a.jpg", photo(), "image/jpeg")},
        data={"quad": json.dumps(QUAD)},
    )
    assert r.status_code == 401
    assert send(client, people, chair, role="manager").status_code == 201
    assert len(scans_of(client, people, chair)) == 1


def test_without_file_storage_it_says_so(client, people, chair):
    r = send(client, people, chair)  # no store fixture: nothing configured
    assert r.status_code == 503 and "storage" in r.json()["detail"]


def test_cookie_writes_need_the_lab_header(client, people, chair, store):
    open_session(client, people, chair, role="owner", mode="edit")
    url = f"/lab/items/{chair['id']}/areas"
    files = {"photo": ("a.jpg", photo(), "image/jpeg")}
    assert (
        client.post(url, files=files, data={"quad": json.dumps(QUAD)}).status_code
        == 403
    )
    files = {"photo": ("a.jpg", photo(), "image/jpeg")}
    r = client.post(
        url, files=files, data={"quad": json.dumps(QUAD)}, headers={"X-Lab-Client": "1"}
    )
    assert r.status_code == 201
    assert (
        client.get(f"/lab/items/{chair['id']}/scans").status_code == 200
    )  # a read needs no header


def test_ordinary_scans_are_unchanged(client, people, chair, store):
    """A phone scan without a marked area: params still come back, now with an empty crop and label."""
    owner = people["owner"]["h"]
    r = client.post(
        "/scans",
        headers=owner,
        json={
            "item_id": chair["id"],
            "source_kind": "photos",
            "params": {"shape": "relief"},
        },
    )
    assert (
        r.status_code == 201
        and r.json()["params"]["crop"] is None
        and r.json()["params"]["label"] is None
    )
    assert (
        client.post(
            "/scans",
            headers=owner,
            json={
                "item_id": chair["id"],
                "source_kind": "photos",
                "params": {"crop": {"quad": [[0, 0]]}},
            },
        ).status_code
        == 422
    )
