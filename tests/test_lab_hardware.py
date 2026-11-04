"""3D Lab: joints on parts, and the hardware list (screws, nails, dowels, clamps)."""

import pytest

from tests.lab_fixtures import PRESETS, chair_parts
from tests.test_lab_auth import open_session

JOINTS = [
    "butt_glue",
    "mortise_tenon",
    "dowel",
    "half_lap",
    "dovetail",
    "pocket_screw",
    "biscuit",
    "other",
]


def put_parts(client, people, chair, parts, role="owner"):
    return client.put(
        f"/lab/items/{chair['id']}/parts",
        headers=people[role]["h"],
        json={"parts": parts},
    )


def put_hw(client, people, chair, lines, role="owner"):
    return client.put(
        f"/lab/items/{chair['id']}/hardware",
        headers=people[role]["h"],
        json={"hardware": lines},
    )


def solved(client, people, chair, role="achari", **params):
    r = client.get(
        f"/lab/items/{chair['id']}", headers=people[role]["h"], params=params
    )
    assert r.status_code == 200, r.text
    return r.json()


def definition(client, people, chair):
    r = client.get(f"/lab/items/{chair['id']}/definition", headers=people["owner"]["h"])
    assert r.status_code == 200, r.text
    return r.json()


def line(kind="screw", name="Wood screw 4 x 50 mm", qty="8", **extra):
    return {"kind": kind, "name": name, "qty_expr": qty, **extra}


# ------------------------------------------------------------------ joints
@pytest.mark.parametrize("joint", JOINTS)
def test_every_listed_joint_is_accepted_and_comes_back(client, people, chair, joint):
    parts = chair_parts()
    parts[5]["joint_type"] = joint
    assert put_parts(client, people, chair, parts).status_code == 200
    by_name = {p["name"]: p for p in definition(client, people, chair)["parts"]}
    assert by_name["Back rail"]["joint_type"] == joint
    out = {p["name"]: p for p in solved(client, people, chair)["parts"]}
    assert out["Back rail"]["joint_type"] == joint
    assert out["Seat"]["joint_type"] is None


@pytest.mark.parametrize("bad", ["glue", "Dowel", "", "mortise", 5])
def test_an_unknown_joint_is_refused(client, people, chair, bad):
    parts = chair_parts()
    parts[0]["joint_type"] = bad
    r = put_parts(client, people, chair, parts)
    assert r.status_code == 422
    assert "joint_type" in r.text


def test_parts_without_joint_type_still_save(client, people, chair):
    """Old clients (desktop, phone) never send joint_type; the free-text note still works."""
    parts = chair_parts()
    assert all("joint_type" not in p and "id" not in p for p in parts)
    assert put_parts(client, people, chair, parts).status_code == 200
    out = solved(client, people, chair)
    assert all(p["joint_type"] is None for p in out["parts"])
    assert out["parts"][-1]["note"].startswith("Mortise")
    # every field the old clients read is still there
    old = {"id", "name", "wood", "l", "w", "t", "axis", "size", "pos", "step", "note"}
    assert old <= set(out["parts"][0])
    assert {"item", "size", "presets", "parts", "cutting_list", "steps"} <= set(out)


# ------------------------------------------------------------------ hardware: validation
def test_hardware_round_trips_in_the_definition(client, people, chair):
    assert definition(client, people, chair)["hardware"] == []
    lines = [
        line(qty="L / 100", assembly_step=2, note="pilot holes first"),
        line("clamp", "Bar clamp 600 mm", "4", assembly_step=3),
    ]
    assert put_hw(client, people, chair, lines).status_code == 200
    got = definition(client, people, chair)["hardware"]
    assert [(g["kind"], g["name"], g["qty_expr"], g["assembly_step"]) for g in got] == [
        ("screw", "Wood screw 4 x 50 mm", "L / 100", 2),
        ("clamp", "Bar clamp 600 mm", "4", 3),
    ]
    assert got[0]["note"] == "pilot holes first" and got[0]["part_id"] is None
    # replaced, not appended
    assert put_hw(client, people, chair, [line()]).status_code == 200
    assert len(definition(client, people, chair)["hardware"]) == 1
    assert put_hw(client, people, chair, []).status_code == 200
    assert definition(client, people, chair)["hardware"] == []


def test_a_negative_quantity_at_one_size_is_refused_with_item_line_and_size(
    client, people, chair
):
    # fine at the standard size (450 - 420 = 30), at M and at L, but -20 at S (400)
    r = put_hw(client, people, chair, [line(), line("nail", "Oval nail", "L - 420")])
    assert r.status_code == 422
    msg = r.json()["detail"]
    assert "Dining Chair" in msg and "line 2" in msg and "size S" in msg
    assert "Oval nail" in msg and "L - 420" in msg and "0 or more" in msg
    assert definition(client, people, chair)["hardware"] == []  # nothing was saved


def test_negative_at_the_standard_size_names_it(client, people, chair):
    r = put_hw(client, people, chair, [line(qty="L - 500")])
    assert r.status_code == 422 and "the standard size" in r.json()["detail"]


@pytest.mark.parametrize(
    "qty", ["", "L +", "sqrt(L)", "X * 2", "L ** 2", "1 / 0", "H - 1000"]
)
def test_bad_quantity_formulas_are_refused(client, people, chair, qty):
    r = put_hw(client, people, chair, [line(qty=qty)])
    assert r.status_code == 422, qty
    assert "line 1" in r.text


def test_quantity_zero_is_allowed_and_left_out_of_the_lists(client, people, chair):
    lines = [line(), line("nail", "Oval nail", "0"), line("clamp", "Clamp", "0")]
    assert put_hw(client, people, chair, lines).status_code == 200
    out = solved(client, people, chair)
    assert [g["kind"] for g in out["hardware"]] == ["screw"]
    assert out["tools"] == []
    assert len(definition(client, people, chair)["hardware"]) == 3  # still editable


def test_bad_kind_step_part_and_line_limit_are_refused(client, people, chair):
    assert put_hw(client, people, chair, [line("rivet")]).status_code == 422
    assert put_hw(client, people, chair, [line(assembly_step=0)]).status_code == 422
    other = "00000000-0000-0000-0000-000000000001"
    r = put_hw(client, people, chair, [line(part_id=other)])
    assert r.status_code == 422 and "part not found" in r.text
    assert (
        put_hw(client, people, chair, [line() for _ in range(100)]).status_code == 200
    )
    assert (
        put_hw(client, people, chair, [line() for _ in range(101)]).status_code == 422
    )


def test_hardware_needs_an_overall_size(client, people):
    h = people["owner"]["h"]
    item = client.post(
        "/items/",
        headers=h,
        json={"sku": "NOSIZE-1", "name": "Bare", "is_furniture": True},
    ).json()
    r = client.put(
        f"/lab/items/{item['id']}/hardware", headers=h, json={"hardware": [line()]}
    )
    assert r.status_code == 422 and "overall size" in r.text


def test_changing_sizes_cannot_break_hardware(client, people, chair):
    lines = [line("nail", "Oval nail", "L - 380")]
    assert put_hw(client, people, chair, lines).status_code == 200
    h = people["owner"]["h"]
    url = f"/lab/items/{chair['id']}"
    r = client.put(
        f"{url}/presets",
        headers=h,
        json={**PRESETS, "S": {"l": 300, "w": 400, "h": 800}},
    )
    assert r.status_code == 422
    assert "hardware line 1" in r.text and "size S" in r.text
    r = client.put(f"{url}/size", headers=h, json={"l": 300, "w": 450, "h": 900})
    assert r.status_code == 422 and "the standard size" in r.text
    assert solved(client, people, chair)["presets"]["S"]["l"] == 400  # unchanged


# ------------------------------------------------------------------ hardware: solved output
def test_quantities_are_whole_numbers_rounded_up_and_follow_the_size(
    client, people, chair
):
    lines = [line(qty="L / 100"), line("nail", "Oval nail", "W / 100 + 0.2")]
    assert put_hw(client, people, chair, lines).status_code == 200

    def qty(**params):
        out = solved(client, people, chair, **params)
        return {g["kind"]: g["items"][0]["qty"] for g in out["hardware"]}

    assert qty() == {"screw": 5, "nail": 5}  # 4.5 and 4.7 round up
    assert qty(size="S") == {"screw": 4, "nail": 5}  # exactly 4.0 stays 4; 4.2 -> 5
    assert qty(size="L") == {"screw": 5, "nail": 6}  # 5.0 stays 5; 5.2 -> 6
    assert qty(l=1000, w=300, h=700) == {"screw": 10, "nail": 4}  # a custom size


def test_clamps_are_tools_by_step_not_stock(client, people, chair):
    before = solved(client, people, chair)["cutting_list"]
    lines = [
        line(assembly_step=2),
        line("dowel", "Dowel 8 x 40 mm", "8", assembly_step=3),
        line("glue", "Wood glue (PVA)", "1"),
        line("clamp", "Bar clamp 600 mm", "4", assembly_step=3),
        line("clamp", "F-clamp 200 mm", "L / 150", assembly_step=2),
        line("clamp", "Strap clamp", "1"),
    ]
    assert put_hw(client, people, chair, lines).status_code == 200
    out = solved(client, people, chair, role="worker")

    # stock hardware: grouped by kind in a fixed order, no clamp anywhere
    assert [g["kind"] for g in out["hardware"]] == ["screw", "dowel", "glue"]
    assert all(i["kind"] != "clamp" for g in out["hardware"] for i in g["items"])

    # tools: per build step, in order, steps with none listed last
    assert [t["step"] for t in out["tools"]] == [2, 3, None]
    by_step = {
        t["step"]: [(i["name"], i["qty"]) for i in t["items"]] for t in out["tools"]
    }
    assert by_step == {
        2: [("F-clamp 200 mm", 3)],  # 450 / 150
        3: [("Bar clamp 600 mm", 4)],
        None: [("Strap clamp", 1)],
    }
    assert out["cutting_list"] == before  # hardware never touches wood totals


def test_a_line_on_a_part_takes_that_parts_step(client, people, chair):
    rail = next(
        p
        for p in definition(client, people, chair)["parts"]
        if p["name"] == "Back rail"
    )  # build step 3
    lines = [
        line("clamp", "Bar clamp", "2", part_id=rail["id"]),
        line("dowel", "Dowel 8 x 40 mm", "4", part_id=rail["id"], assembly_step=1),
    ]
    assert put_hw(client, people, chair, lines).status_code == 200
    out = solved(client, people, chair)
    assert [t["step"] for t in out["tools"]] == [3]
    dowel = out["hardware"][0]["items"][0]
    assert dowel["step"] == 1 and dowel["part_id"] == rail["id"]  # explicit step wins


def test_hardware_stays_on_its_part_when_parts_are_saved_with_their_ids(
    client, people, chair
):
    d = definition(client, people, chair)
    rail = next(p for p in d["parts"] if p["name"] == "Back rail")
    assert put_hw(client, people, chair, [line(part_id=rail["id"])]).status_code == 200

    # the editor sends each part's id back: the link survives, and the id is kept
    assert put_parts(client, people, chair, d["parts"]).status_code == 200
    d2 = definition(client, people, chair)
    assert {p["id"] for p in d2["parts"]} == {p["id"] for p in d["parts"]}
    assert d2["hardware"][0]["part_id"] == rail["id"]

    # an old client sends no ids: parts are new, so the link is cleared, the line stays
    assert put_parts(client, people, chair, chair_parts()).status_code == 200
    d3 = definition(client, people, chair)
    assert len(d3["hardware"]) == 1 and d3["hardware"][0]["part_id"] is None

    # removing the part clears the link too
    rail = next(p for p in d3["parts"] if p["name"] == "Back rail")
    assert put_hw(client, people, chair, [line(part_id=rail["id"])]).status_code == 200
    rest = [p for p in d3["parts"] if p["name"] != "Back rail"]
    assert put_parts(client, people, chair, rest).status_code == 200
    assert definition(client, people, chair)["hardware"][0]["part_id"] is None


def test_a_part_id_from_another_item_or_a_repeat_is_refused(client, people, chair):
    d = definition(client, people, chair)
    stranger = {**d["parts"][0], "id": "00000000-0000-0000-0000-000000000002"}
    r = put_parts(client, people, chair, [stranger])
    assert r.status_code == 422 and "not a part of this item" in r.text
    twice = [d["parts"][0], d["parts"][0]]
    assert put_parts(client, people, chair, twice).status_code == 422


# ------------------------------------------------------------------ who can do what
def test_only_owner_and_manager_edit_hardware_everyone_in_the_workshop_views(
    client, people, chair
):
    for role in ("achari", "worker", "artisan", "sales"):
        assert put_hw(client, people, chair, [line()], role=role).status_code == 403
    assert (
        client.put(
            f"/lab/items/{chair['id']}/hardware", json={"hardware": []}
        ).status_code
        == 401
    )
    assert put_hw(client, people, chair, [line()], role="manager").status_code == 200

    for role in ("owner", "manager", "achari", "worker", "artisan"):
        out = solved(client, people, chair, role=role)
        assert out["hardware"][0]["items"][0]["name"] == "Wood screw 4 x 50 mm", role
    r = client.get(f"/lab/items/{chair['id']}", headers=people["sales"]["h"])
    assert r.status_code == 403
    # the raw formulas are for editors only
    r = client.get(
        f"/lab/items/{chair['id']}/definition", headers=people["worker"]["h"]
    )
    assert r.status_code == 403


def test_cookie_edits_to_hardware_need_the_lab_client_header(client, people, chair):
    open_session(client, people, chair, role="owner", mode="edit")
    url = f"/lab/items/{chair['id']}/hardware"
    body = {"hardware": [line()]}
    assert client.put(url, json=body).status_code == 403
    assert client.put(url, json=body, headers={"X-Lab-Client": "1"}).status_code == 200


# ------------------------------------------------------------------ a worked bed
def test_a_bed_with_ledger_screws_dowels_and_clamps(client, people):
    """Mirrors the worked example in docs/design/scan-and-lab.md."""
    h = people["owner"]["h"]
    bed = client.post(
        "/items/",
        headers=h,
        json={
            "sku": "BED-HW-1",
            "name": "Queen bed",
            "dimensions_mm": {"l": 2100, "w": 1600, "h": 1000},
            "is_furniture": True,
        },
    ).json()
    sizes = {
        "S": {"l": 2000, "w": 1400, "h": 1000},
        "M": {"l": 2100, "w": 1600, "h": 1000},
        "L": {"l": 2200, "w": 1800, "h": 1000},
    }
    assert (
        client.put(f"/lab/items/{bed['id']}/presets", headers=h, json=sizes).status_code
        == 200
    )
    post = {
        "wood_type": "Teak",
        "l_expr": "H",
        "w_expr": "80",
        "t_expr": "80",
        "axis": "y",
    }
    rail = {"wood_type": "Teak", "l_expr": "W - 160", "w_expr": "150", "t_expr": "40",
            "axis": "z", "joint_type": "dowel", "assembly_step": 2}  # fmt: skip
    ledger = {"wood_type": "Teak", "l_expr": "L - 160", "w_expr": "40", "t_expr": "40",
              "assembly_step": 2, "joint_type": "pocket_screw"}  # fmt: skip
    parts = [
        {**post, "name": "Post", "x_expr": x, "z_expr": z}
        for x in ("0", "L-80")
        for z in ("0", "W-80")
    ] + [{**rail, "name": "Head rail", "x_expr": "0", "z_expr": "80"},
         {**ledger, "name": "Side ledger", "x_expr": "80", "z_expr": "0"}]  # fmt: skip
    r = client.put(f"/lab/items/{bed['id']}/parts", headers=h, json={"parts": parts})
    assert r.status_code == 200, r.text
    hw = [
        line("screw", "Wood screw 4 x 50 mm", "4 * (L - 160) / 200", assembly_step=2,
             note="4 per slat ledger, one slat every 200 mm"),
        line("dowel", "Dowel 10 x 60 mm", "8", assembly_step=2, note="rail to post, 2 per joint"),
        line("bracket", "Bed rail bracket set (plates, bolt, nut)", "4", assembly_step=2,
             note="bolt-on bed fitting: stays in the bed, so stock, not a clamp tool"),
        line("glue", "Wood glue (PVA)", "1", assembly_step=2),
        line("clamp", "Bar clamp 1200 mm", "4", assembly_step=2),
        line("clamp", "F-clamp 300 mm", "4", assembly_step=1),
    ]  # fmt: skip
    r = client.put(f"/lab/items/{bed['id']}/hardware", headers=h, json={"hardware": hw})
    assert r.status_code == 200, r.text
    out = r.json()
    screws = out["hardware"][0]["items"][0]
    assert screws["qty"] == 39  # 4 * 1940 / 200 = 38.8 -> 39
    assert [g["kind"] for g in out["hardware"]] == ["screw", "dowel", "bracket", "glue"]
    assert [(t["step"], [i["qty"] for i in t["items"]]) for t in out["tools"]] == [
        (1, [4]),
        (2, [4]),
    ]
    small = client.get(
        f"/lab/items/{bed['id']}", headers=h, params={"size": "S"}
    ).json()
    assert small["hardware"][0]["items"][0]["qty"] == 37  # 4 * 1840 / 200 = 36.8 -> 37


def test_lab_pages_are_valid_utf8():
    """A stray Latin-1 byte (an editor on Windows) shows as a broken character in the page."""
    from pathlib import Path

    folder = Path(__file__).resolve().parent.parent / "app" / "static" / "lab"
    for page in [*folder.glob("*.js"), *folder.glob("*.html"), *folder.glob("*.css")]:
        page.read_bytes().decode("utf-8")  # raises on a bad byte
