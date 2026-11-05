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
    "door-traditional-frame": 17,
    "tv-unit-2-door": 11,
    "bookshelf-open": 9,
    "chest-box": 10,
    "stool-4-leg": 13,
    "dressing-table": 14,
    "diwan-couch": 15,
    "wall-panel-deity": 6,
    "jhula-swing": 7,
    "pillar-carved": 5,
}

# the categories in the shop's desktop app (Stock, Categories) and what has no template
SHOP_CATEGORIES = [
    "Chest", "Dining Table", "Diwana Set", "Dressing Table", "King Size Bed", "Queen Size bed",
    "Simha Dwaram", "Single Deity", "Stools", "Study Tables", "Teapoi", "Wooden Chair",
    "Wooden Jhula", "Wooden pillars", "Wooden Shelves", "Wood Pannel",
]  # fmt: skip
NO_TEMPLATE = ["Raja Rani Dolls"]  # carved figures: a scanned sculpture, not parts
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


def test_the_table_template_also_covers_coffee_and_side_tables(client, people):
    for name, height in (("Coffee table", 450), ("Side table", 550)):
        parts, out, s = solved_template(client, people, "table-dining", name)
        assert (
            parts["Table top"]["pos"]["y"] + parts["Table top"]["t"] == height == s["h"]
        )


def test_the_traditional_door_set_the_leaf_sits_in_the_frame_and_the_panels_meet_the_rails(
    client, people
):
    parts, out, s = solved_template(client, people, "door-traditional-frame")
    L, H = s["l"], s["h"]
    top_of = lambda p: p["pos"]["y"] + p["size"]["y"]  # noqa: E731
    right_of = lambda p: p["pos"]["x"] + p["size"]["x"]  # noqa: E731
    assert len(parts) == 17 and parts["Crown"]["pos"]["y"] + parts["Crown"]["t"] == H
    left, right = parts["Door stile (left)"], parts["Door stile (right)"]
    assert (
        left["pos"]["x"] == parts["Frame jamb (left)"]["w"] == 300
    )  # the leaf starts where the jamb ends
    assert right_of(right) == L - 300  # and ends where the right jamb begins
    leaf_top = top_of(left)
    assert (
        leaf_top == H - 700 and parts["Carved frieze"]["pos"]["y"] == leaf_top
    )  # the frieze sits on the leaf
    assert top_of(parts["Door bottom rail"]) == parts["Lower panel (left)"]["pos"]["y"]
    assert top_of(parts["Lower panel (left)"]) == parts["Door middle rail"]["pos"]["y"]
    assert top_of(parts["Door middle rail"]) == parts["Upper panel (left)"]["pos"]["y"]
    assert top_of(parts["Upper panel (left)"]) == parts["Door top rail"]["pos"]["y"]
    assert (
        right_of(parts["Upper panel (left)"])
        == parts["Door muntin (upper)"]["pos"]["x"]
    )  # panels meet the muntin
    assert (
        parts["Door muntin (upper)"]["pos"]["x"] + 80
        == parts["Upper panel (right)"]["pos"]["x"]
    )
    assert right_of(parts["Upper panel (right)"]) == right["pos"]["x"]
    stock = {i["name"]: i["qty"] for g in out["hardware"] for i in g["items"]}
    assert stock["Brass stud (large)"] == 9 and stock["Heavy door hinge 150 mm"] == 4


def test_the_tv_unit_has_an_open_middle_bay_between_two_doors(client, people):
    parts, out, s = solved_template(client, people, "tv-unit-2-door")
    L = s["l"]
    left, right = parts["Door (left)"], parts["Door (right)"]
    assert (
        left["w"] == right["w"] and left["pos"]["x"] + left["w"] < right["pos"]["x"]
    )  # a gap between the doors
    assert right["pos"]["x"] + right["w"] <= L and left["pos"]["z"] == 0
    shelf = parts["Shelf (middle bay)"]
    d1, d2 = parts["Divider (left)"], parts["Divider (right)"]
    assert (
        shelf["pos"]["x"] == d1["pos"]["x"] + d1["size"]["x"]
    )  # the shelf fits between the dividers
    assert shelf["pos"]["x"] + shelf["size"]["x"] == d2["pos"]["x"]
    assert len(parts) == 11


def test_the_bookshelf_shelves_are_evenly_spaced(client, people):
    for size_name in ("Low", "Standard", "Tall and wide"):
        parts, out, s = solved_template(client, people, "bookshelf-open", size_name)
        ys = [
            parts[n]["pos"]["y"]
            for n in ("Shelf (lowest)", "Shelf (middle)", "Shelf (highest)")
        ]
        bottom, top = parts["Bottom shelf"], parts["Top"]
        gaps = [
            ys[0] - (bottom["pos"]["y"] + bottom["t"]),
            ys[1] - ys[0] - 22,
            ys[2] - ys[1] - 22,
            top["pos"]["y"] - ys[2] - 22,
        ]
        assert max(gaps) - min(gaps) < 0.1 and min(gaps) > 100, (
            size_name,
            gaps,
        )  # four equal openings


def _category(db, name):
    from app.models.category import Category

    found = db.query(Category).filter(Category.name == name).first()
    if not found:
        found = Category(name=name)
        db.add(found)
        db.commit()
        db.refresh(found)
    return found


def test_every_shop_category_has_a_template_and_a_typical_size_that_exist():
    templates = lab_templates.all_templates()
    for name in SHOP_CATEGORIES:
        sg = lab_templates.suggestion(name)
        assert sg is not None, name
        assert (
            sg["template"] in templates and sg["size"] is not None
        ), name  # a size by that name exists
        assert set(sg["size"]) == {"name", "l", "w", "h"}
    for name in NO_TEMPLATE:
        assert lab_templates.suggestion(name) is None, name
    assert (
        lab_templates.suggestion(None) is None
        and lab_templates.suggestion("Spaceship") is None
    )
    # matched ignoring capitals and extra spaces
    assert (
        lab_templates.suggestion("  queen   SIZE bed ")["template"]
        == "bed-square-posts"
    )
    assert (
        lab_templates.suggestion("King Size Bed")["template"] == "bed-crown-headboard"
    )
    assert (
        lab_templates.suggestion("Simha Dwaram")["template"] == "door-traditional-frame"
    )
    assert lab_templates.suggestion("Teapoi")["size"]["name"] == "Teapoy"


def test_the_editor_is_told_which_template_fits_the_products_category(
    client, people, db
):
    owner = people["owner"]["h"]
    cat = _category(db, "King Size Bed")
    item = client.post(
        "/items/", headers=owner,
        json={"sku": f"K-{uuid.uuid4().hex[:6]}", "name": "King", "is_furniture": True, "category_id": str(cat.id)},
    ).json()  # fmt: skip
    d = definition(client, people, item)
    assert d["category"] == "King Size Bed"
    sg = d["suggestion"]
    assert sg["template"] == "bed-crown-headboard" and sg["size"] == {
        "name": "King",
        "l": 2185,
        "w": 1980,
        "h": 1300,
    }
    # no category, or one with no template (the dolls): no suggestion
    plain = new_piece(client, people, size=SIZES["queen"])
    assert definition(client, people, plain)["suggestion"] is None
    dolls = client.post(
        "/items/", headers=owner,
        json={"sku": f"D-{uuid.uuid4().hex[:6]}", "name": "Dolls", "is_furniture": True, "category_id": str(_category(db, "Raja Rani Dolls").id)},
    ).json()  # fmt: skip
    d2 = definition(client, people, dolls)
    assert d2["category"] == "Raja Rani Dolls" and d2["suggestion"] is None


def top_of(p):
    return p["pos"]["y"] + p["size"]["y"]


def test_the_chest_lid_sits_on_the_walls_and_the_walls_on_the_bottom(client, people):
    parts, out, s = solved_template(client, people, "chest-box")
    assert top_of(parts["Lid"]) == s["h"] and parts["Lid"]["pos"]["y"] == top_of(
        parts["Front wall"]
    )
    assert (
        parts["Front wall"]["pos"]["y"] == top_of(parts["Bottom"])
        and top_of(parts["Bottom"]) == 85
    )
    assert (
        len([n for n in parts if n.startswith("Foot")]) == 4
        and parts["Foot (front left)"]["l"] == 60
    )


def test_the_stool_seat_is_at_the_full_height_on_four_legs(client, people):
    parts, out, s = solved_template(client, people, "stool-4-leg", "Tall")
    assert top_of(parts["Seat"]) == s["h"] == 600
    legs = [p for n, p in parts.items() if n.startswith("Leg")]
    assert len(legs) == 4 and {p["l"] for p in legs} == {s["h"] - 30}


def test_the_dressing_table_top_is_at_750_and_the_mirror_frame_reaches_the_full_height(
    client, people
):
    parts, out, s = solved_template(client, people, "dressing-table", "Wide")
    assert top_of(parts["Top"]) == 750 and top_of(parts["Mirror top rail"]) == s["h"]
    assert (
        parts["Mirror upright (left)"]["pos"]["y"] == 750
        and top_of(parts["Mirror upright (left)"]) == s["h"]
    )
    glass = [
        i for g in out["hardware"] for i in g["items"] if i["name"] == "Mirror glass"
    ]
    assert glass and glass[0]["qty"] == 1


def test_the_diwan_seat_platform_back_and_arms_stack_correctly(client, people):
    parts, out, s = solved_template(client, people, "diwan-couch", "4 seater")
    assert (
        parts["Seat platform"]["pos"]["y"] == 320
        and top_of(parts["Back top rail"]) == s["h"]
    )
    assert (
        top_of(parts["Back panel"]) == parts["Back top rail"]["pos"]["y"]
    )  # the panel runs up to the top rail
    assert (
        parts["Back panel"]["pos"]["y"] == top_of(parts["Seat platform"]) == 350
    )  # the back starts at the seat
    assert (
        top_of(parts["Arm panel (left)"]) == parts["Arm cap (left)"]["pos"]["y"] == 600
    )  # the arm cap sits on the arm
    assert (
        parts["Front post (left)"]["l"] == 600
        and parts["Back post (left)"]["l"] == s["h"]
    )
    stock = {i["name"] for g in out["hardware"] for i in g["items"]}
    assert {"Seat cushion (foam)", "Back cushion (foam)", "Bolster"} <= stock


def test_the_deity_panel_has_a_carved_insert_that_fills_the_frame_in_front_of_the_back_board(
    client, people
):
    for name in ("Small", "Standard", "Big"):
        parts, out, s = solved_template(client, people, "wall-panel-deity", name)
        insert, back = parts["Carved insert"], parts["Back board"]
        left, right = parts["Frame (left)"], parts["Frame (right)"]
        assert (
            insert["pos"]["x"] == left["w"]
            and insert["pos"]["x"] + insert["w"] == right["pos"]["x"]
        )
        assert insert["pos"]["y"] == parts["Frame (bottom)"]["size"]["y"]
        assert top_of(insert) == parts["Frame (top)"]["pos"]["y"]
        assert (
            insert["pos"]["z"] == 0
            and insert["pos"]["z"] + insert["size"]["z"] == back["pos"]["z"]
        )  # the front is the carved face


def test_the_jhula_seat_sits_a_quarter_up_and_the_crest_tops_the_back(client, people):
    for name in ("2 seater", "3 seater", "4 seater"):
        parts, out, s = solved_template(client, people, "jhula-swing", name)
        assert abs(parts["Seat board"]["pos"]["y"] - s["h"] * 0.25) < 0.01
        assert (
            top_of(parts["Back panel"]) == parts["Back crest"]["pos"]["y"]
            and top_of(parts["Back crest"]) == s["h"]
        )
        assert parts["Back panel"]["pos"]["y"] == top_of(parts["Seat board"])
    stock = {i["name"]: i["qty"] for g in out["hardware"] for i in g["items"]}
    assert stock["Eye bolt 12 mm"] == 4 and stock["Swing chain set (four chains)"] == 1


def test_the_pillar_sections_stack_without_gaps(client, people):
    for name in ("House pillar", "Standard", "Large"):
        parts, out, s = solved_template(client, people, "pillar-carved", name)
        assert top_of(parts["Base"]) == parts["Lower shaft"]["pos"]["y"]
        assert (
            abs(top_of(parts["Lower shaft"]) - parts["Collar ring"]["pos"]["y"]) < 0.02
        )
        assert (
            abs(top_of(parts["Collar ring"]) - parts["Upper shaft"]["pos"]["y"]) < 0.02
        )
        assert (
            abs(top_of(parts["Upper shaft"]) - parts["Capital"]["pos"]["y"]) < 0.02
            and top_of(parts["Capital"]) == s["h"]
        )


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
