"""3D Lab: solving a furniture piece for a size, the cutting list, who may edit."""

import uuid

import pytest

from tests.lab_fixtures import CHAIR_SIZE, PRESETS, chair_parts


def get(client, people, item, role="achari", **params):
    return client.get(
        f"/lab/items/{item['id']}", headers=people[role]["h"], params=params
    )


# ------------------------------------------------------------------ solving
def test_default_size_solves_the_parts(client, people, chair):
    r = get(client, people, chair)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["size"] == {
        "label": "Standard",
        **{k: float(v) for k, v in CHAIR_SIZE.items()},
    }
    by_name = {}
    for p in out["parts"]:
        by_name.setdefault(p["name"], []).append(p)
    assert len(out["parts"]) == 6 and out["steps"] == 3

    seat = by_name["Seat"][0]
    assert (seat["l"], seat["w"], seat["t"]) == (450, 450, 30)
    assert seat["size"] == {
        "x": 450,
        "y": 30,
        "z": 450,
    }  # axis x: length along left-right
    assert seat["pos"] == {"x": 0, "y": 450, "z": 0}
    back_leg = by_name["Back leg"][1]
    assert back_leg["size"] == {"x": 40, "y": 900, "z": 40}  # axis y: length runs up
    assert back_leg["pos"] == {"x": 410, "y": 0, "z": 410}
    assert by_name["Back rail"][0]["note"].startswith("Mortise")


def test_cutting_list_matches_hand_worked_volumes(client, people, chair):
    cl = get(client, people, chair).json()["cutting_list"]
    assert cl["pieces"] == 6
    teak = cl["by_wood"][0]
    assert teak["wood"] == "Teak" and teak["pieces"] == 6
    rows = {r["name"]: r for r in teak["items"]}
    assert rows["Front leg"]["qty"] == 2 and (
        rows["Front leg"]["l"],
        rows["Front leg"]["w"],
    ) == (450, 40)
    assert rows["Back leg"]["qty"] == 2 and rows["Back leg"]["l"] == 900
    # Seat 450x450x30 = 6,075,000 mm3; front legs 2x(450x40x40) = 1,440,000;
    # back legs 2x(900x40x40) = 2,880,000; back rail 370x60x20 = 444,000 -> 10,839,000 mm3
    assert rows["Seat"]["volume_m3"] == pytest.approx(0.006075)
    assert rows["Front leg"]["volume_m3"] == pytest.approx(0.00144)
    assert cl["total_m3"] == pytest.approx(0.010839)


@pytest.mark.parametrize("size,total", [("S", None), ("M", 0.010839), ("L", 0.012804)])
def test_presets_recalculate_everything(client, people, chair, size, total):
    out = get(client, people, chair, size=size).json()
    assert out["size"]["label"] == {"S": "Small", "M": "Medium", "L": "Large"}[size]
    dims = PRESETS[size]
    legs = [p for p in out["parts"] if p["name"] == "Back leg"]
    assert all(p["l"] == dims["h"] for p in legs)  # back legs are H tall
    assert [p["pos"]["x"] for p in legs] == [0, dims["l"] - 40]
    if total:  # L: seat 7.5e6 + front legs 3.2e6/2... worked in the design notes
        assert out["cutting_list"]["total_m3"] == pytest.approx(total)
    assert out["presets"].keys() == {"S", "M", "L"}


def test_custom_size_recalculates_and_lower_case_size_works(client, people, chair):
    out = get(client, people, chair, l=600, w=500, h=1100).json()
    assert out["size"] == {"label": "Custom", "l": 600.0, "w": 500.0, "h": 1100.0}
    seat = next(p for p in out["parts"] if p["name"] == "Seat")
    assert (seat["l"], seat["w"]) == (600, 500)
    assert get(client, people, chair, size="m").json()["size"]["label"] == "Medium"


@pytest.mark.parametrize(
    "params,code",
    [
        ({"l": 600, "w": 500}, 422),  # need all three
        ({"l": 10, "w": 500, "h": 900}, 422),  # below 50 mm
        ({"l": 600, "w": 9000, "h": 900}, 422),  # above 5000 mm
        ({"size": "X"}, 422),  # not S/M/L
    ],
)
def test_bad_sizes_are_refused(client, people, chair, params, code):
    assert get(client, people, chair, **params).status_code == code


def test_viewing_never_changes_anything(client, people, chair):
    before = client.get(
        f"/lab/items/{chair['id']}/definition", headers=people["owner"]["h"]
    ).json()
    get(client, people, chair, size="L")
    get(client, people, chair, l=700, w=700, h=700)
    after = client.get(
        f"/lab/items/{chair['id']}/definition", headers=people["owner"]["h"]
    ).json()
    assert before == after
    item = client.get(f"/items/{chair['id']}", headers=people["owner"]["h"]).json()
    assert item["dimensions_mm"] == CHAIR_SIZE


# ------------------------------------------------------------------ who can do what
def test_workshop_roles_can_view_but_not_edit(client, people, chair):
    for role in ("owner", "manager", "achari", "worker"):
        assert get(client, people, chair, role=role).status_code == 200, role
    assert get(client, people, chair, role="sales").status_code == 403
    assert client.get(f"/lab/items/{chair['id']}").status_code == 401

    body = {"parts": chair_parts()[:1]}
    for role in ("achari", "worker", "sales"):
        h = people[role]["h"]
        assert (
            client.put(
                f"/lab/items/{chair['id']}/parts", headers=h, json=body
            ).status_code
            == 403
        ), role
        assert (
            client.put(
                f"/lab/items/{chair['id']}/presets", headers=h, json=PRESETS
            ).status_code
            == 403
        )
        assert (
            client.put(
                f"/lab/items/{chair['id']}/furniture",
                headers=h,
                json={"is_furniture": False},
            ).status_code
            == 403
        )
        assert (
            client.get(f"/lab/items/{chair['id']}/definition", headers=h).status_code
            == 403
        )
    assert (
        client.put(
            f"/lab/items/{chair['id']}/parts", headers=people["manager"]["h"], json=body
        ).status_code
        == 200
    )
    assert len(get(client, people, chair).json()["parts"]) == 1


def test_only_furniture_is_in_the_lab(client, people, chair):
    plain = client.post(
        "/items/",
        headers=people["owner"]["h"],
        json={"sku": f"PL-{uuid.uuid4().hex[:6]}", "name": "Plank"},
    ).json()
    assert get(client, people, plain).status_code == 404
    listed = client.get("/lab/items", headers=people["achari"]["h"]).json()
    mine = next(i for i in listed if i["id"] == chair["id"])
    assert mine["parts"] == 6 and mine["sizes"] == ["S", "M", "L"]
    assert plain["id"] not in [i["id"] for i in listed]
    off = client.put(
        f"/lab/items/{chair['id']}/furniture",
        headers=people["owner"]["h"],
        json={"is_furniture": False},
    )
    assert off.status_code == 200
    assert chair["id"] not in [
        i["id"] for i in client.get("/lab/items", headers=people["achari"]["h"]).json()
    ]


# ------------------------------------------------------------------ definitions are validated when saved
def put_parts(client, people, item, parts):
    return client.put(
        f"/lab/items/{item['id']}/parts",
        headers=people["owner"]["h"],
        json={"parts": parts},
    )


def test_a_bad_formula_is_refused_with_the_part_and_field(client, people, chair):
    bad = dict(chair_parts()[0], l_expr="L ** 2")
    r = put_parts(client, people, chair, [bad])
    assert r.status_code == 422
    assert "Seat" in r.json()["detail"] and "length" in r.json()["detail"]
    assert len(get(client, people, chair).json()["parts"]) == 6  # nothing was replaced


def test_a_formula_that_breaks_at_one_preset_is_refused(client, people, chair):
    # fine at 450 and above, but 0 or negative at the Small size (400)
    tricky = dict(chair_parts()[0], l_expr="L - 400")
    r = put_parts(client, people, chair, [tricky])
    assert r.status_code == 422
    assert "size S" in r.json()["detail"] and "above 0" in r.json()["detail"]


def test_changing_presets_cannot_break_existing_parts(client, people, chair):
    tight = {
        "S": {"l": 100, "w": 100, "h": 100}
    }  # leg width 40 is fine, back rail L-80 = 20, ok; H-150 < 0 only for y position
    ok = client.put(
        f"/lab/items/{chair['id']}/presets",
        headers=people["owner"]["h"],
        json={"S": {"l": 60, "w": 450, "h": 900}},
    )
    assert ok.status_code == 422  # back rail L-80 would be negative
    still = client.get(
        f"/lab/items/{chair['id']}/definition", headers=people["owner"]["h"]
    ).json()
    assert still["presets"]["S"] == {"l": 400.0, "w": 400.0, "h": 800.0}  # unchanged
    assert tight


def test_parts_need_an_overall_size_first(client, people):
    item = client.post(
        "/items/",
        headers=people["owner"]["h"],
        json={
            "sku": f"NS-{uuid.uuid4().hex[:6]}",
            "name": "No size",
            "is_furniture": True,
        },
    ).json()
    r = put_parts(client, people, item, chair_parts()[:1])
    assert r.status_code == 422 and "overall size" in r.json()["detail"]


def test_definition_round_trips_for_the_editor(client, people, chair):
    d = client.get(
        f"/lab/items/{chair['id']}/definition", headers=people["owner"]["h"]
    ).json()
    assert d["item"]["is_furniture"] is True and len(d["parts"]) == 6
    again = client.put(
        f"/lab/items/{chair['id']}/parts",
        headers=people["owner"]["h"],
        json={"parts": d["parts"]},
    )
    assert (
        again.status_code == 200
    )  # what the editor loaded can be saved back unchanged
    assert len(get(client, people, chair).json()["parts"]) == 6


def test_axis_and_step_limits(client, people, chair):
    assert (
        put_parts(client, people, chair, [dict(chair_parts()[0], axis="q")]).status_code
        == 422
    )
    assert (
        put_parts(
            client, people, chair, [dict(chair_parts()[0], assembly_step=0)]
        ).status_code
        == 422
    )
    assert (
        put_parts(client, people, chair, [dict(chair_parts()[0], name="")]).status_code
        == 422
    )
    too_many = [dict(chair_parts()[0], name=f"P{i}") for i in range(151)]
    assert put_parts(client, people, chair, too_many).status_code == 422


def test_standard_size_can_be_changed_but_never_breaks_the_parts(client, people, chair):
    h = people["owner"]["h"]
    url = f"/lab/items/{chair['id']}/size"
    ok = client.put(url, headers=h, json={"l": 480, "w": 480, "h": 950})
    assert ok.status_code == 200
    assert get(client, people, chair).json()["size"]["l"] == 480
    # back rail is L - 80: a 60 mm wide chair would give 60 - 80 < 0, so it is refused
    bad = client.put(url, headers=h, json={"l": 60, "w": 480, "h": 950})
    assert bad.status_code == 422 and "Back rail" in bad.json()["detail"]
    assert get(client, people, chair).json()["size"]["l"] == 480  # unchanged
    assert (
        client.put(url, headers=h, json={"l": 10, "w": 480, "h": 950}).status_code
        == 422
    )
    assert (
        client.put(
            url, headers=people["achari"]["h"], json={"l": 480, "w": 480, "h": 950}
        ).status_code
        == 403
    )


def test_editor_and_viewer_pages_are_served(client, people, chair):
    for page in ("view", "edit"):
        r = client.get(f"/lab/{page}/{chair['id']}")
        assert r.status_code == 200 and "text/html" in r.headers["content-type"]
        assert (
            "<script" in r.text
            and "unsafe-inline"
            not in r.headers["content-security-policy"].split("style-src")[0]
        )
    js = client.get(
        "/lab/static/vendor/three.module.js", headers={"Accept-Encoding": "gzip"}
    )
    assert js.status_code == 200 and js.headers.get("content-encoding") == "gzip"
