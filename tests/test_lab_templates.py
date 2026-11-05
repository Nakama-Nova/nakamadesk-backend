"""3D Lab: start a piece from a built-in template, or copy the parts of another piece."""

import uuid

import pytest

from app.services import lab_templates
from tests.test_lab_auth import open_session
from tests.test_lab_carvings import carving, design_with_picture

SIZES = {
    "queen": {"l": 2185, "w": 1675, "h": 1270},
    "king": {"l": 2185, "w": 1980, "h": 1300},
    "double": {"l": 2185, "w": 1370, "h": 1200},
    "single": {"l": 2000, "w": 1000, "h": 1100},
}
EXPECTED = {  # template id -> number of parts
    "bed-square-posts": 33,
    "bed-crown-headboard": 25,
    "table-dining": 9,
    "chair-dining": 12,
    "wardrobe-2-door": 12,
    "door-panelled": 7,
}
CASES = [
    (t["id"], s["name"])
    for t in lab_templates.all_templates().values()
    for s in t["typical_sizes"]
]


def new_piece(client, people, name="New bed", size=None, presets=None):
    h = people["owner"]["h"]
    body = {"sku": f"T-{uuid.uuid4().hex[:7]}", "name": name, "is_furniture": True}
    if size:
        body["dimensions_mm"] = size
    item = client.post("/items/", headers=h, json=body).json()
    if presets:
        r = client.put(f"/lab/items/{item['id']}/presets", headers=h, json=presets)
        assert r.status_code == 200, r.text
    return item


def apply(client, people, item, template="bed-square-posts", role="owner", **extra):
    return client.post(
        f"/lab/items/{item['id']}/apply-template",
        headers=people[role]["h"],
        json={"template": template, **extra},
    )


def copy(client, people, item, source, role="owner", **extra):
    return client.post(
        f"/lab/items/{item['id']}/copy-parts",
        headers=people[role]["h"],
        json={"source_item_id": source["id"], **extra},
    )


def definition(client, people, item):
    r = client.get(f"/lab/items/{item['id']}/definition", headers=people["owner"]["h"])
    assert r.status_code == 200, r.text
    return r.json()


def box(solved):
    """The box the parts fill: (x, y, z) from lowest to highest."""
    lo, hi = [1e9] * 3, [-1e9] * 3
    for p in solved["parts"]:
        for i, a in enumerate("xyz"):
            lo[i] = min(lo[i], p["pos"][a])
            hi[i] = max(hi[i], p["pos"][a] + p["size"][a])
    return lo, hi


# ------------------------------------------------------------------ the templates themselves
def test_the_shipped_templates_load_and_are_listed(client, people):
    assert set(lab_templates.all_templates()) == set(EXPECTED)
    r = client.get("/lab/templates", headers=people["owner"]["h"])
    assert r.status_code == 200
    by_id = {t["id"]: t for t in r.json()}
    for tid, parts in EXPECTED.items():
        t = by_id[tid]
        assert t["parts"] == parts and t["hardware"] >= 4, tid
        assert t["carve"] and t["description"] and len(t["typical_sizes"]) >= 3, tid
        assert all({"name", "l", "w", "h"} <= set(s) for s in t["typical_sizes"])
    sq = by_id["bed-square-posts"]
    assert sq["size_hint"] == SIZES["queen"] and "square" in sq["name"].lower()
    for role in ("achari", "worker", "artisan", "sales"):
        assert (
            client.get("/lab/templates", headers=people[role]["h"]).status_code == 403
        )
    assert client.get("/lab/templates").status_code == 401
    assert (
        client.get("/lab/templates", headers=people["manager"]["h"]).status_code == 200
    )


@pytest.mark.parametrize("tid,size_name", CASES)
def test_every_template_fits_every_typical_size_exactly_and_no_parts_overlap(
    client, people, tid, size_name
):
    size = next(
        s
        for s in lab_templates.all_templates()[tid]["typical_sizes"]
        if s["name"] == size_name
    )
    dims = {k: size[k] for k in ("l", "w", "h")}
    item = new_piece(client, people, size=dims)
    r = apply(client, people, item, tid)
    assert r.status_code == 200, r.text
    out = r.json()
    lo, hi = box(out)
    # nothing sticks out of the overall box, and nothing leaves it short
    assert lo == [0, 0, 0] and hi == [dims["l"], dims["h"], dims["w"]]
    assert all(p["l"] > 0 and p["w"] > 0 and p["t"] > 0 for p in out["parts"])
    boxes = [
        (
            p["name"],
            [p["pos"][a] for a in "xyz"],
            [p["pos"][a] + p["size"][a] for a in "xyz"],
        )
        for p in out["parts"]
    ]
    for n, (name1, a1, b1) in enumerate(boxes):
        for name2, a2, b2 in boxes[n + 1 :]:
            overlap = [min(b1[i], b2[i]) - max(a1[i], a2[i]) for i in range(3)]
            assert not all(
                o > 0.5 for o in overlap
            ), f"{name1} overlaps {name2}"  # touching is fine


def solved_template(client, people, tid, size_name=None):
    t = lab_templates.all_templates()[tid]
    size = next(
        s
        for s in t["typical_sizes"]
        if s["name"] == (size_name or t["typical_sizes"][0]["name"])
    )
    item = new_piece(client, people, size={k: size[k] for k in ("l", "w", "h")})
    out = apply(client, people, item, tid).json()
    return {p["name"]: p for p in out["parts"]}, out, size


def test_the_dining_table_is_a_top_on_four_legs_with_aprons(client, people):
    parts, out, s = solved_template(client, people, "table-dining", "6 seater")
    top = parts["Table top"]
    assert (top["l"], top["w"], top["t"]) == (s["l"], s["w"], 40) and top["pos"][
        "y"
    ] == s["h"] - 40
    legs = [p for n, p in parts.items() if n.startswith("Leg")]
    assert len(legs) == 4 and {p["l"] for p in legs} == {
        s["h"] - 40
    }  # the top's upper face is H
    assert (
        parts["Apron (front)"]["l"] == s["l"] - 280
        and parts["Apron (left)"]["l"] == s["w"] - 280
    )
    assert {t["step"] for t in out["tools"]} == {
        1
    }  # the clamps hold the leg and apron glue-up


def test_the_dining_chair_has_a_450_mm_seat_and_a_back_that_reaches_the_full_height(
    client, people
):
    parts, out, s = solved_template(client, people, "chair-dining")
    seat = parts["Seat"]
    assert seat["pos"]["y"] + seat["t"] == 450  # the seat height
    assert (
        parts["Front leg (left)"]["l"] == 420
        and parts["Back leg (left)"]["l"] == s["h"]
    )
    rail = parts["Back top rail"]
    assert rail["pos"]["y"] + rail["size"]["y"] == s["h"]
    assert parts["Back splat"]["l"] == s["h"] - 550  # between the seat and the top rail
    assert len(parts) == 12


def test_the_wardrobe_has_two_doors_that_cover_the_front_and_a_rail_and_shelves(
    client, people
):
    parts, out, s = solved_template(client, people, "wardrobe-2-door", "2 door, 1200")
    left, right = parts["Door (left)"], parts["Door (right)"]
    assert left["w"] == right["w"] == s["l"] / 2 - 3  # a 3 mm gap between them
    assert (
        left["pos"]["z"] == 0
        and left["t"] == 25
        and left["pos"]["x"] + left["w"] < right["pos"]["x"]
    )
    assert parts["Side (left)"]["size"]["y"] == s["h"] and "Hanging rail" in parts
    assert "Shelf (lower)" in parts and "Shelf (upper)" in parts
    stock = {i["name"]: i["qty"] for g in out["hardware"] for i in g["items"]}
    assert stock["Door hinge 100 mm"] == 6 and stock["Door handle"] == 2


def test_the_panelled_door_frame_and_panels_meet_exactly(client, people):
    parts, out, s = solved_template(client, people, "door-panelled")
    assert len(parts) == 7 and parts["Stile (left)"]["l"] == s["h"]
    lower, upper = parts["Lower panel"], parts["Upper panel"]
    bottom, lock, top = parts["Bottom rail"], parts["Lock rail"], parts["Top rail"]
    top_of = lambda p: p["pos"]["y"] + p["size"]["y"]  # noqa: E731
    assert lower["pos"]["y"] == top_of(bottom) and top_of(lower) == lock["pos"]["y"]
    assert upper["pos"]["y"] == top_of(lock) and top_of(upper) == top["pos"]["y"]
    assert parts["Upper panel"]["w"] == s["l"] - 200  # fills between the stiles
    stock = {i["name"]: i["qty"] for g in out["hardware"] for i in g["items"]}
    assert stock["Door hinge 100 mm"] == 3 and stock["Mortise lock"] == 1


def test_the_square_post_bed_matches_the_queen_sheet(client, people):
    out = apply(client, people, new_piece(client, people, size=SIZES["queen"])).json()
    parts = {}
    for p in out["parts"]:
        parts.setdefault(p["name"], []).append(p)
    assert (
        parts["Head post (front)"][0]["l"] == 1270
    )  # the headboard height on the sheet
    assert (
        parts["Foot post (front)"][0]["l"] == 530
    )  # the footboard height on the sheet
    rail = parts["Side rail (front)"][0]
    assert rail["l"] == 1981  # the mattress space on the sheet: 1981 x 1524 mm
    assert parts["Bed slat"][0]["l"] == 1524 and len(parts["Bed slat"]) == 12
    cutting = {
        r["name"]: r["qty"] for w in out["cutting_list"]["by_wood"] for r in w["items"]
    }
    assert cutting["Bed slat"] == 12 and cutting["Head post (front)"] == 1


def test_a_template_brings_hardware_and_clamps_as_tools(client, people):
    out = apply(client, people, new_piece(client, people, size=SIZES["queen"])).json()
    stock = {i["name"]: i["qty"] for g in out["hardware"] for i in g["items"]}
    assert (
        stock["Bed rail bracket set (plates, bolt, nut)"] == 4
    )  # the bed fittings are stock, not tools
    assert stock["Wood screw 4 x 50 mm"] == 48 and "Bar clamp 1500 mm" not in stock
    assert {t["step"] for t in out["tools"]} == {2, 3}
    none = apply(
        client, people, new_piece(client, people, size=SIZES["queen"]), hardware=False
    ).json()
    assert none["hardware"] == [] and none["tools"] == []


def test_wood_goes_on_every_part_and_the_copy_is_the_pieces_own(client, people):
    item = new_piece(client, people, size=SIZES["queen"])
    out = apply(client, people, item, wood_type="Teak").json()
    assert {p["wood"] for p in out["parts"]} == {"Teak"} and out["cutting_list"][
        "by_wood"
    ][0]["wood"] == "Teak"
    d = definition(client, people, item)
    assert len(d["parts"]) == 33 and len(d["hardware"]) == 7
    # editing the piece's copy does not touch the template or another piece
    other = apply(client, people, new_piece(client, people, size=SIZES["king"])).json()
    assert {p["wood"] for p in other["parts"]} == {None}


# ------------------------------------------------------------------ refusals
def test_a_template_needs_the_piece_to_have_a_size_first(client, people):
    r = apply(client, people, new_piece(client, people))
    assert r.status_code == 422 and "overall size" in r.json()["detail"]


def test_a_template_that_breaks_at_a_preset_is_refused_with_the_size(client, people):
    # H - 890 must stay above zero: fine at the standard size, but not at preset S
    item = new_piece(
        client,
        people,
        size=SIZES["queen"],
        presets={"S": {"l": 2000, "w": 1000, "h": 800}},
    )
    r = apply(client, people, item)
    assert r.status_code == 422 and "size S" in r.json()["detail"]
    assert definition(client, people, item)["parts"] == []  # nothing was saved


def test_a_piece_with_parts_is_not_overwritten_unless_asked(client, people):
    item = new_piece(client, people, size=SIZES["queen"])
    assert apply(client, people, item).status_code == 200
    r = apply(client, people, item, "bed-crown-headboard")
    assert r.status_code == 409 and "33 parts" in r.json()["detail"]
    assert len(definition(client, people, item)["parts"]) == 33  # untouched
    r = apply(client, people, item, "bed-crown-headboard", replace=True)
    assert r.status_code == 200 and len(definition(client, people, item)["parts"]) == 25


def test_replacing_clears_the_old_hardware_and_carvings_too(client, people):
    item = new_piece(client, people, size=SIZES["queen"])
    apply(client, people, item)
    d = definition(client, people, item)
    lattice = next(p for p in d["parts"] if p["name"] == "Headboard lattice panel")
    design = design_with_picture(client, people, "Lattice")
    r = client.put(
        f"/lab/items/{item['id']}/carvings",
        headers=people["owner"]["h"],
        json={"carvings": [carving(lattice, design, face="right", margin_mm=10)]},
    )
    assert r.status_code == 200, r.text
    out = apply(
        client, people, item, "bed-crown-headboard", replace=True, hardware=False
    ).json()
    d2 = definition(client, people, item)
    assert d2["carvings"] == [] and d2["hardware"] == [] and out["designs"] == []


def test_unknown_template_and_unknown_piece(client, people):
    item = new_piece(client, people, size=SIZES["queen"])
    assert apply(client, people, item, "wardrobe-of-doom").status_code == 404
    ghost = {"id": str(uuid.uuid4())}
    assert apply(client, people, ghost).status_code == 404


def test_only_owner_and_manager_start_from_a_template_and_cookie_writes_need_the_header(
    client, people
):
    item = new_piece(client, people, size=SIZES["queen"])
    for role in ("achari", "worker", "artisan", "sales"):
        assert apply(client, people, item, role=role).status_code == 403, role
    assert (
        client.post(
            f"/lab/items/{item['id']}/apply-template",
            json={"template": "bed-square-posts"},
        ).status_code
        == 401
    )
    assert apply(client, people, item, role="manager").status_code == 200
    again = new_piece(client, people, size=SIZES["queen"])
    open_session(client, people, again, role="owner", mode="edit")
    url = f"/lab/items/{again['id']}/apply-template"
    assert client.post(url, json={"template": "bed-square-posts"}).status_code == 403
    assert (
        client.post(
            url, json={"template": "bed-square-posts"}, headers={"X-Lab-Client": "1"}
        ).status_code
        == 200
    )


# ------------------------------------------------------------------ copy from another piece
def test_copy_parts_from_another_piece_fits_the_new_size(client, people, chair):
    queen = new_piece(
        client, people, "Chair, big", size={"l": 600, "w": 600, "h": 1200}
    )
    r = copy(client, people, queen, chair)
    assert r.status_code == 200, r.text
    out = r.json()
    assert len(out["parts"]) == 6
    back = [p for p in out["parts"] if p["name"] == "Back leg"]
    assert all(p["l"] == 1200 for p in back)  # the formula H, at the new size
    src = definition(
        client, people, chair
    )  # the source is untouched, and the ids are the copy's own
    mine = definition(client, people, queen)
    assert len(src["parts"]) == 6 and {p["id"] for p in src["parts"]}.isdisjoint(
        {p["id"] for p in mine["parts"]}
    )
    assert [p["name"] for p in mine["parts"]] == [p["name"] for p in src["parts"]]


def test_copy_follows_hardware_and_carvings_to_the_new_parts(client, people, chair):
    owner = people["owner"]["h"]
    seat = next(
        p for p in definition(client, people, chair)["parts"] if p["name"] == "Seat"
    )
    assert (
        client.put(
            f"/lab/items/{chair['id']}/hardware",
            headers=owner,
            json={
                "hardware": [
                    {
                        "kind": "screw",
                        "name": "Wood screw 4 x 50 mm",
                        "qty_expr": "8",
                        "part_id": seat["id"],
                    },
                    {
                        "kind": "clamp",
                        "name": "Bar clamp",
                        "qty_expr": "2",
                        "assembly_step": 2,
                    },
                ]
            },
        ).status_code
        == 200
    )
    design = design_with_picture(client, people, "Seat lotus")
    assert (
        client.put(
            f"/lab/items/{chair['id']}/carvings",
            headers=owner,
            json={"carvings": [carving(seat, design, face="top", margin_mm=20)]},
        ).status_code
        == 200
    )

    twin = new_piece(client, people, "Chair twin", size={"l": 500, "w": 500, "h": 1000})
    out = copy(client, people, twin, chair).json()
    mine = definition(client, people, twin)
    new_seat = next(p for p in mine["parts"] if p["name"] == "Seat")
    assert new_seat["id"] != seat["id"]
    assert {h["part_id"] for h in mine["hardware"]} == {
        new_seat["id"],
        None,
    }  # the link moved to the copy's seat
    assert [(c["part_id"], c["design_id"], c["face"]) for c in mine["carvings"]] == [
        (new_seat["id"], design["id"], "top")
    ]
    assert out["designs"][0]["placements"] == 1

    bare = new_piece(client, people, "Chair bare", size={"l": 500, "w": 500, "h": 1000})
    copy(client, people, bare, chair, hardware=False, carvings=False)
    d = definition(client, people, bare)
    assert len(d["parts"]) == 6 and d["hardware"] == [] and d["carvings"] == []


def test_copy_refusals(client, people, chair):
    target = new_piece(client, people, size={"l": 500, "w": 500, "h": 1000})
    assert copy(client, people, chair, chair).status_code == 422  # itself
    empty = new_piece(client, people, "Empty", size=SIZES["queen"])
    r = copy(client, people, target, empty)
    assert r.status_code == 422 and "no parts to copy" in r.json()["detail"]
    nosize = new_piece(client, people, "No size")
    r = copy(client, people, nosize, chair)
    assert r.status_code == 422 and "overall size" in r.json()["detail"]
    tiny = new_piece(
        client, people, "Tiny", size={"l": 60, "w": 60, "h": 60}
    )  # chair formulas go negative
    r = copy(client, people, tiny, chair)
    assert r.status_code == 422 and "standard size" in r.json()["detail"]
    assert definition(client, people, tiny)["parts"] == []
    assert (
        client.post(
            f"/lab/items/{target['id']}/copy-parts",
            headers=people["owner"]["h"],
            json={"source_item_id": str(uuid.uuid4())},
        ).status_code
        == 404
    )
    assert copy(client, people, target, chair).status_code == 200
    assert copy(client, people, target, chair).status_code == 409  # now it has parts
    assert copy(client, people, target, chair, replace=True).status_code == 200


def test_who_may_copy(client, people, chair):
    target = new_piece(client, people, size={"l": 500, "w": 500, "h": 1000})
    for role in ("achari", "worker", "artisan", "sales"):
        assert copy(client, people, target, chair, role=role).status_code == 403, role
    assert (
        client.post(
            f"/lab/items/{target['id']}/copy-parts",
            json={"source_item_id": chair["id"]},
        ).status_code
        == 401
    )
    assert copy(client, people, target, chair, role="manager").status_code == 200
