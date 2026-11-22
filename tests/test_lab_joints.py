"""3D Lab: where the joints sit, found from the solved parts."""

import json
from pathlib import Path

from app.models.furniture_part import FurniturePart
from app.services import lab_service
from app.services.lab_joints import find_joints

QUEEN = {"l": 2185.0, "w": 1675.0, "h": 1270.0}


def part(name, size, pos, joint="mortise_tenon", step=1, note=None):
    return {
        "id": name,
        "name": name,
        "size": dict(zip("xyz", size)),
        "pos": dict(zip("xyz", pos)),
        "joint_type": joint,
        "note": note,
        "step": step,
    }


def test_a_rail_meeting_a_post_is_a_joint_at_the_contact():
    post = part("post", (100, 1000, 100), (0, 0, 0), joint=None)
    rail = part(
        "rail",
        (60, 100, 500),
        (20, 200, 100),
        joint="mortise_tenon",
        step=2,
        note="tenon",
    )
    (j,) = find_joints([post, rail])
    assert (j["a"], j["b"], j["kind"], j["step"], j["note"]) == (
        0,
        1,
        "mortise_tenon",
        2,
        "tenon",
    )
    assert j["pos"] == {"x": 50.0, "y": 250.0, "z": 100.0} and j["size"] == {
        "x": 60.0,
        "y": 100.0,
        "z": 0.0,
    }


def test_apart_edge_only_and_undeclared_contacts_are_not_joints():
    post = part("post", (100, 1000, 100), (0, 0, 0), joint=None)
    assert find_joints([post, part("far", (50, 50, 50), (500, 0, 0))]) == []
    assert (
        find_joints([post, part("corner", (50, 50, 50), (100, 1000, 100))]) == []
    )  # meets at a corner
    assert (
        find_joints([post, part("plain", (50, 50, 50), (100, 0, 0), joint=None)]) == []
    )  # nobody says it is joined


def test_dowels_and_screws_are_spread_along_the_joint():
    ledger = part("ledger", (1000, 40, 40), (0, 0, 0), joint=None)
    slat = part("slat", (90, 20, 600), (500, 40, 0), joint="pocket_screw")
    (j,) = find_joints([ledger, slat])
    assert [p["x"] for p in j["points"]] == [545.0] and len(
        j["points"]
    ) == 1  # contact is 90 mm long: one screw
    wide = part("top", (1000, 20, 40), (0, 40, 0), joint="dowel")
    (j,) = find_joints([ledger, wide])
    assert (
        len(j["points"]) == 6 and j["points"][0]["x"] < j["points"][-1]["x"]
    )  # 1000 mm / 150 mm


def test_hardware_tied_to_a_part_is_listed_on_its_joints():
    post = part("post", (100, 1000, 100), (0, 0, 0), joint=None)
    rail = part("rail", (60, 100, 500), (20, 200, 100))
    hw = [
        {"part_id": "rail", "name": "Dowel 10 x 60", "qty": 4},
        {"part_id": "x", "name": "Other", "qty": 4},
        {"part_id": "rail", "name": "None", "qty": 0},
    ]
    assert find_joints([post, rail], hw)[0]["hardware"] == ["Dowel 10 x 60"]


def test_the_bed_template_gets_joints_at_its_posts_and_slats():
    tpl = json.loads(
        (
            Path(lab_service.__file__).parents[1]
            / "lab_templates"
            / "bed-square-posts.json"
        ).read_text("utf-8")
    )
    solved = [lab_service.solve_part(FurniturePart(**p), QUEEN) for p in tpl["parts"]]
    joints = find_joints(solved)
    names = lambda j: (solved[j["a"]]["name"], solved[j["b"]]["name"])  # noqa: E731
    assert len(joints) >= 20
    assert any(
        set(names(j)) == {"Head post (front)", "Headboard bottom rail"} for j in joints
    )
    assert (
        sum(1 for j in joints if "Bed slat" in names(j)[0] + names(j)[1]) >= 24
    )  # 12 slats on 2 ledgers
    for j in joints:  # every joint lies inside the bed
        assert (
            -1 <= j["pos"]["x"] <= QUEEN["l"] + 1
            and -1 <= j["pos"]["z"] <= QUEEN["w"] + 1
        )
