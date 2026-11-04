"""3D Lab: CNC designs carved on furniture parts. A design is stored once, placed many times."""

import io
import os
import uuid

import pytest
from PIL import Image

from app.core.config import settings
from app.models.design import Design
from tests.lab_fixtures import chair_parts

PANEL = {"l_expr": "H - 100", "w_expr": "(L - 30) / 2", "t_expr": "20", "axis": "y"}


def png(color=None, size=(60, 40)) -> bytes:
    """A small picture. Each call is a different one (the same bytes may only be stored once)."""
    color = color or tuple(os.urandom(3))
    out = io.BytesIO()
    Image.new("RGB", size, color).save(out, "PNG")
    return out.getvalue()


@pytest.fixture(params=["store", "disk"])
def where(request, tmp_path, monkeypatch):
    """Design pictures in the object store (production) and on the disk (development)."""
    if request.param == "store":
        return request.getfixturevalue("store")
    monkeypatch.setattr(settings, "MEDIA_DIR", str(tmp_path / "media"))
    return None


def new_design(client, people, name="Lotus panel", **extra):
    r = client.post(
        "/designs", headers=people["artisan"]["h"], json={"name": name, **extra}
    )
    assert r.status_code == 201, r.text
    return r.json()


def set_image(client, people, design, data=None, role="artisan", ctype="image/png"):
    return client.put(
        f"/designs/{design['id']}/image",
        headers=people[role]["h"],
        files={"file": ("pic.png", data if data is not None else png(), ctype)},
    )


def design_with_picture(client, people, name="Lotus panel"):
    """A design with a picture; `d["data"]` holds the picture's bytes."""
    d = new_design(client, people, name)
    data = png()
    assert set_image(client, people, d, data).status_code == 200
    return {**d, "data": data}


@pytest.fixture
def cabinet(client, people):
    """A cabinet with two door panels, set up by the owner."""
    h = people["owner"]["h"]
    item = client.post(
        "/items/",
        headers=h,
        json={
            "sku": f"CAB-{uuid.uuid4().hex[:6]}",
            "name": "Cabinet",
            "dimensions_mm": {"l": 900, "w": 600, "h": 1200},
            "is_furniture": True,
        },
    ).json()
    presets = {
        "S": {"l": 800, "w": 500, "h": 1000},
        "M": {"l": 900, "w": 600, "h": 1200},
        "L": {"l": 1000, "w": 700, "h": 1400},
    }
    assert (
        client.put(
            f"/lab/items/{item['id']}/presets", headers=h, json=presets
        ).status_code
        == 200
    )
    parts = [
        {"name": "Left door", "x_expr": "10", "y_expr": "50", **PANEL},
        {"name": "Right door", "x_expr": "(L + 10) / 2", "y_expr": "50", **PANEL},
    ]
    r = client.put(f"/lab/items/{item['id']}/parts", headers=h, json={"parts": parts})
    assert r.status_code == 200, r.text
    return item


def definition(client, people, item):
    r = client.get(f"/lab/items/{item['id']}/definition", headers=people["owner"]["h"])
    assert r.status_code == 200, r.text
    return r.json()


def put_carvings(client, people, item, carvings, role="owner"):
    return client.put(
        f"/lab/items/{item['id']}/carvings",
        headers=people[role]["h"],
        json={"carvings": carvings},
    )


def solved(client, people, item, role="achari", **params):
    r = client.get(f"/lab/items/{item['id']}", headers=people[role]["h"], params=params)
    assert r.status_code == 200, r.text
    return r.json()


def carving(part, design, **extra):
    return {"part_id": part["id"], "design_id": design["id"], "face": "front", **extra}


def doors(client, people, item):
    parts = {p["name"]: p for p in definition(client, people, item)["parts"]}
    return parts["Left door"], parts["Right door"]


# ------------------------------------------------------------------ design category and picture
def test_designs_have_a_category_and_default_to_general(client, people):
    plain = new_design(client, people, "Nandi")
    assert plain["category"] == "general" and plain["has_image"] is False
    fur = new_design(client, people, "Bed headboard", category="furniture")
    assert fur["category"] == "furniture"
    h = people["artisan"]["h"]
    listed = client.get("/designs", headers=h, params={"category": "furniture"}).json()
    assert [d["id"] for d in listed] == [fur["id"]]
    r = client.patch(
        f"/designs/{plain['id']}", headers=h, json={"category": "furniture"}
    )
    assert r.status_code == 200 and r.json()["category"] == "furniture"
    r = client.post("/designs", headers=h, json={"name": "x", "category": "toy"})
    assert r.status_code == 422


def test_a_picture_is_kept_private_and_served_back(client, people, db, where):
    d = new_design(client, people)
    data = png()
    r = set_image(client, people, d, data)
    assert r.status_code == 200 and r.json()["has_image"] is True
    got = client.get(f"/designs/{d['id']}/image", headers=people["artisan"]["h"])
    assert got.status_code == 200 and got.content == data
    assert got.headers["content-type"] == "image/png"
    assert "private" in got.headers["cache-control"]
    key = db.query(Design).filter(Design.id == d["id"]).one().image_key
    assert key.startswith("private/designs/")
    assert client.get(f"/media/{key}").status_code == 404  # never a public URL


def test_replacing_a_picture_removes_the_old_file(client, people, db, store):
    d = design_with_picture(client, people)
    old = db.query(Design).filter(Design.id == d["id"]).one().image_key
    assert old in store.objects
    assert set_image(client, people, d, png((1, 2, 3))).status_code == 200
    db.expire_all()
    new = db.query(Design).filter(Design.id == d["id"]).one().image_key
    assert new != old and old not in store.objects and new in store.objects
    # the same bytes again on the same design is not a duplicate
    assert set_image(client, people, d, png((1, 2, 3))).status_code == 200


def test_the_same_picture_is_stored_once(client, people):
    first = design_with_picture(client, people, "Lotus panel")
    other = new_design(client, people, "Lotus copy")
    r = set_image(client, people, other, first["data"])  # identical bytes
    assert r.status_code == 409
    assert first["sku"] in r.json()["detail"] and "already stored" in r.json()["detail"]
    assert set_image(client, people, other, png((9, 9, 9))).status_code == 200


def test_only_real_pictures_are_accepted(client, people):
    d = new_design(client, people)
    assert set_image(client, people, d, b"not a picture").status_code == 400
    assert set_image(client, people, d, b"hello", ctype="text/plain").status_code == 415
    glb = b"glTF" + b"\x00" * 20
    assert (
        set_image(client, people, d, glb, ctype="model/gltf-binary").status_code == 415
    )
    assert set_image(client, people, d, b"").status_code == 400


def test_design_pictures_are_for_owner_and_artisan(client, people):
    d = new_design(client, people)
    for role in ("manager", "sales", "achari", "worker"):
        assert set_image(client, people, d, role=role).status_code == 403, role
    assert set_image(client, people, d, role="owner").status_code == 200
    for role in ("manager", "sales", "achari", "worker"):
        r = client.get(f"/designs/{d['id']}/image", headers=people[role]["h"])
        assert r.status_code == 403, role


# ------------------------------------------------------------------ carvings
def test_a_design_on_both_doors_is_one_design_and_two_placements(
    client, people, cabinet, where
):
    design = design_with_picture(client, people)
    left, right = doors(client, people, cabinet)
    rows = [
        carving(left, design, margin_mm=30, note="lotus, upright"),
        carving(right, design, margin_mm=30, mirror=True),
    ]
    r = put_carvings(client, people, cabinet, rows)
    assert r.status_code == 200, r.text

    out = solved(client, people, cabinet, role="worker")
    assert len(out["carvings"]) == 2
    assert [d["placements"] for d in out["designs"]] == [2]  # stored once, used twice
    assert out["designs"][0]["sku"] == design["sku"]
    first, second = out["carvings"]
    assert (first["part"], first["mirror"], first["note"]) == (
        "Left door",
        False,
        "lotus, upright",
    )
    assert second["part"] == "Right door" and second["mirror"] is True
    # standard size 900 x 600 x 1200: a door is (900 - 30) / 2 = 435 wide and 1100 tall, less 2 x 30
    assert first["area_mm"] == {"w": 375, "h": 1040}
    assert first["image_url"] == f"/lab/designs/{design['id']}/image"
    # the area follows the size
    small = solved(client, people, cabinet, size="S")["carvings"][0]["area_mm"]
    assert small == {"w": 325, "h": 840}  # (800 - 30) / 2 - 60, 1000 - 100 - 60

    # the designs library knows where it is used, and filed it under furniture
    h = people["artisan"]["h"]
    detail = client.get(f"/designs/{design['id']}", headers=h).json()
    assert detail["category"] == "furniture"
    assert [(u["part_name"], u["face"]) for u in detail["furniture"]] == [
        ("Left door", "front"),
        ("Right door", "front"),
    ]


def test_the_definition_lists_carvings_and_the_designs_to_choose_from(
    client, people, cabinet
):
    design = design_with_picture(client, people)
    bare = new_design(client, people, "No picture yet")
    left, _ = doors(client, people, cabinet)
    assert (
        put_carvings(
            client, people, cabinet, [carving(left, design, face="back")]
        ).status_code
        == 200
    )
    d = definition(client, people, cabinet)
    assert [(c["part_id"], c["design_id"], c["face"]) for c in d["carvings"]] == [
        (left["id"], design["id"], "back")
    ]
    by_id = {x["id"]: x for x in d["designs"]}
    assert by_id[design["id"]]["has_image"] is True
    assert by_id[bare["id"]]["has_image"] is False
    assert {"id", "sku", "name", "category", "has_image"} == set(by_id[bare["id"]])


def test_bad_carvings_are_refused(client, people, cabinet, chair):
    design = design_with_picture(client, people)
    bare = new_design(client, people, "No picture yet")
    left, _ = doors(client, people, cabinet)

    r = put_carvings(client, people, cabinet, [carving(left, bare)])
    assert r.status_code == 422 and "no picture" in r.json()["detail"]
    assert bare["sku"] in r.json()["detail"]

    r = put_carvings(client, people, cabinet, [carving(left, design, face="side")])
    assert r.status_code == 422

    ghost = {**carving(left, design), "design_id": str(uuid.uuid4())}
    assert (
        "design not found"
        in put_carvings(client, people, cabinet, [ghost]).json()["detail"]
    )

    seat = next(
        p for p in definition(client, people, chair)["parts"] if p["name"] == "Seat"
    )
    r = put_carvings(client, people, cabinet, [carving(seat, design)])
    assert r.status_code == 422 and "part not found" in r.json()["detail"]

    assert (
        put_carvings(
            client, people, cabinet, [carving(left, design, margin_mm=-1)]
        ).status_code
        == 422
    )
    many = [carving(left, design) for _ in range(61)]
    assert put_carvings(client, people, cabinet, many).status_code == 422
    assert definition(client, people, cabinet)["carvings"] == []  # nothing was saved


def test_a_margin_that_leaves_no_room_at_one_size_is_refused(client, people, cabinet):
    design = design_with_picture(client, people)
    left, _ = doors(client, people, cabinet)
    # a door is 435 wide at the standard size but 385 at S: a 190 mm margin passes M, fails S
    r = put_carvings(client, people, cabinet, [carving(left, design, margin_mm=195)])
    assert r.status_code == 422
    msg = r.json()["detail"]
    assert "Cabinet" in msg and "carving line 1" in msg and "size S" in msg
    assert design["sku"] in msg and "Left door" in msg and "195 mm margin" in msg
    assert (
        put_carvings(
            client, people, cabinet, [carving(left, design, margin_mm=190)]
        ).status_code
        == 200
    )


def test_changing_sizes_cannot_leave_a_carving_without_room(client, people, cabinet):
    design = design_with_picture(client, people)
    left, _ = doors(client, people, cabinet)
    assert (
        put_carvings(
            client, people, cabinet, [carving(left, design, margin_mm=150)]
        ).status_code
        == 200
    )
    h = people["owner"]["h"]
    tiny = {
        "S": {"l": 600, "w": 500, "h": 1000}
    }  # door 285 wide, 2 x 150 margin leaves < 0
    r = client.put(f"/lab/items/{cabinet['id']}/presets", headers=h, json=tiny)
    assert r.status_code == 422 and "carving line 1" in r.text


def test_only_owner_and_manager_edit_carvings(client, people, cabinet):
    design = design_with_picture(client, people)
    left, _ = doors(client, people, cabinet)
    rows = [carving(left, design)]
    for role in ("achari", "worker", "artisan", "sales"):
        assert (
            put_carvings(client, people, cabinet, rows, role=role).status_code == 403
        ), role
    r = client.put(f"/lab/items/{cabinet['id']}/carvings", json={"carvings": rows})
    assert r.status_code == 401
    assert (
        put_carvings(client, people, cabinet, rows, role="manager").status_code == 200
    )
    for role in ("owner", "manager", "achari", "worker", "artisan"):
        assert len(solved(client, people, cabinet, role=role)["carvings"]) == 1, role


def test_carvings_stay_on_their_part_when_parts_are_saved_with_their_ids(
    client, people, cabinet
):
    design = design_with_picture(client, people)
    d = definition(client, people, cabinet)
    left = next(p for p in d["parts"] if p["name"] == "Left door")
    assert (
        put_carvings(client, people, cabinet, [carving(left, design)]).status_code
        == 200
    )

    h = people["owner"]["h"]
    url = f"/lab/items/{cabinet['id']}/parts"
    assert client.put(url, headers=h, json={"parts": d["parts"]}).status_code == 200
    assert definition(client, people, cabinet)["carvings"][0]["part_id"] == left["id"]
    assert len(solved(client, people, cabinet)["carvings"]) == 1

    # a client that sends no ids makes new parts: the carving is kept but unattached
    plain = [
        {k: v for k, v in p.items() if k not in ("id", "sort")} for p in d["parts"]
    ]
    assert client.put(url, headers=h, json={"parts": plain}).status_code == 200
    kept = definition(client, people, cabinet)["carvings"]
    assert len(kept) == 1 and kept[0]["part_id"] is None
    assert solved(client, people, cabinet)["carvings"] == []  # nothing to draw it on
    assert solved(client, people, cabinet)["designs"] == []


# ------------------------------------------------------------------ the picture in the Lab
def test_lab_viewers_get_the_picture_of_a_placed_design_only(
    client, people, cabinet, where
):
    design = design_with_picture(client, people)
    loose = design_with_picture(client, people, "Not placed")
    left, _ = doors(client, people, cabinet)
    assert (
        put_carvings(client, people, cabinet, [carving(left, design)]).status_code
        == 200
    )

    for role in ("owner", "manager", "achari", "worker", "artisan"):
        r = client.get(f"/lab/designs/{design['id']}/image", headers=people[role]["h"])
        assert r.status_code == 200 and r.content == design["data"], role
        assert "private" in r.headers["cache-control"]
    # a design nobody carved is not served through the Lab, and not to other roles
    assert (
        client.get(
            f"/lab/designs/{loose['id']}/image", headers=people["achari"]["h"]
        ).status_code
        == 404
    )
    assert (
        client.get(
            f"/lab/designs/{design['id']}/image", headers=people["sales"]["h"]
        ).status_code
        == 403
    )
    assert client.get(f"/lab/designs/{design['id']}/image").status_code == 401


def test_the_lab_cookie_can_load_a_carving_picture(client, people, cabinet):
    from tests.test_lab_auth import open_session

    design = design_with_picture(client, people)
    left, _ = doors(client, people, cabinet)
    assert (
        put_carvings(client, people, cabinet, [carving(left, design)]).status_code
        == 200
    )
    open_session(client, people, cabinet, role="achari")
    assert client.get(f"/lab/designs/{design['id']}/image").status_code == 200


def test_old_clients_are_unaffected(client, people, chair):
    """No carvings: the solved item just has two more, empty, lists."""
    r = client.get(f"/lab/items/{chair['id']}", headers=people["achari"]["h"]).json()
    assert r["carvings"] == [] and r["designs"] == []
    assert {"item", "size", "presets", "parts", "cutting_list", "steps"} <= set(r)
    d = definition(client, people, chair)
    assert d["carvings"] == []
    # saving parts the way the desktop does (no ids, no joints) still works
    h = people["owner"]["h"]
    r = client.put(
        f"/lab/items/{chair['id']}/parts", headers=h, json={"parts": chair_parts()}
    )
    assert r.status_code == 200
