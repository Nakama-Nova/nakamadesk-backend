"""
Designs (DES-0001) and their link to restorations and products, bought pieces that go
through finishing and painting (with a cost summary), expenses on a project, and
sculpture scans being the artisans' own work.
"""

import re
import uuid
from decimal import Decimal

import pytest

from app.models.restoration import RestorationFile
from tests.test_restoration import new_project
from tests.test_scans import PC, claim, put_result, queue

OUTSIDERS = ("manager", "sales", "achari", "worker")


def D(x):
    return Decimal(str(x))


def new_design(client, h, name="Dwarapalaka", **extra):
    r = client.post("/designs", headers=h, json={"name": name, **extra})
    assert r.status_code == 201, r.text
    return r.json()


def new_item(client, h, name="Piece", **extra):
    r = client.post(
        "/items/", headers=h, json={"name": name, "selling_price": 1000, **extra}
    )
    assert r.status_code == 200, r.text
    return r.json()


def number(sku):
    return int(sku.split("-")[1])


# ------------------------------------------------------------------ designs
def test_designs_get_their_own_codes_in_order(client, people):
    h = people["artisan"]["h"]
    a, b = new_design(client, h, "Nandi"), new_design(client, h, "Garuda")
    assert (
        re.fullmatch(r"DES-\d{4,}", a["sku"])
        and number(b["sku"]) == number(a["sku"]) + 1
    )
    assert (
        a["status"] == "draft"
        and a["product_count"] == 0
        and a["restoration_id"] is None
    )


def test_a_design_comes_from_a_restoration_and_shows_its_files(client, people, db):
    owner, artisan = people["owner"]["h"], people["artisan"]["h"]
    project = new_project(client, owner)
    for kind in ("glb", "stl"):
        db.add(
            RestorationFile(
                restoration_id=project["id"],
                kind=kind,
                key=f"k/{uuid.uuid4().hex}.{kind}",
                content_type="x",
                size_bytes=10,
            )
        )
    db.commit()
    d = new_design(
        client,
        artisan,
        "Door guardian",
        restoration_id=project["id"],
        description="Left jamb",
    )
    assert d["restoration_title"] == project["title"]
    detail = client.get(f"/designs/{d['id']}", headers=owner).json()
    assert (
        detail["restoration"]["id"] == project["id"]
        and detail["restoration"]["kind"] == "temple"
    )
    assert {f["kind"] for f in detail["files"]} == {"glb", "stl"}
    back = client.get(
        f"/restorations/{project['id']}", headers=owner
    ).json()  # and the restoration lists its designs
    assert [x["sku"] for x in back["designs"]] == [d["sku"]]
    assert (
        client.post(
            "/designs",
            headers=owner,
            json={"name": "x", "restoration_id": str(uuid.uuid4())},
        ).status_code
        == 404
    )


def test_products_and_designs_link_both_ways(client, people):
    h = people["owner"]["h"]
    design = new_design(client, h)
    other = new_design(client, h, "Garuda")
    a = new_item(client, h, "Carved Nandi", item_type="sculpture")
    b = new_item(client, h, "Big Nandi", item_type="sculpture")

    r = client.post(
        f"/designs/{design['id']}/products", headers=h, json={"item_id": a["id"]}
    )
    assert r.status_code == 200 and [p["sku"] for p in r.json()] == [a["sku"]]
    again = client.post(
        f"/designs/{design['id']}/products", headers=h, json={"item_id": a["id"]}
    )
    assert (
        again.status_code == 200 and len(again.json()) == 1
    )  # linking twice changes nothing
    client.post(
        f"/designs/{design['id']}/products", headers=h, json={"item_id": b["id"]}
    )
    client.post(
        f"/designs/{other['id']}/products", headers=h, json={"item_id": a["id"]}
    )  # one product, two designs

    assert (
        client.get(f"/designs/{design['id']}", headers=h).json()["product_count"] == 2
    )
    from_product = client.get(f"/designs?item_id={a['id']}", headers=h).json()
    assert {d["sku"] for d in from_product} == {
        design["sku"],
        other["sku"],
    }  # the other direction
    assert [
        d["sku"] for d in client.get(f"/designs?item_id={b['id']}", headers=h).json()
    ] == [design["sku"]]

    gone = client.delete(f"/designs/{design['id']}/products/{b['id']}", headers=h)
    assert gone.status_code == 200 and [p["sku"] for p in gone.json()] == [a["sku"]]
    assert (
        client.delete(
            f"/designs/{design['id']}/products/{b['id']}", headers=h
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"/designs/{design['id']}/products",
            headers=h,
            json={"item_id": str(uuid.uuid4())},
        ).status_code
        == 404
    )


def test_building_a_product_out_of_a_design(client, people):
    h = people["artisan"]["h"]
    design = new_design(
        client, h, "Hoysala Madhanika", description="Lady with the mirror"
    )
    p = client.post(f"/designs/{design['id']}/create-product", headers=h, json={})
    assert p.status_code == 201, p.text
    made = p.json()
    assert (
        made["name"] == "Hoysala Madhanika"
        and made["item_type"] == "sculpture"
        and made["sku"].startswith("SCU-")
    )
    custom = client.post(
        f"/designs/{design['id']}/create-product",
        headers=h,
        json={"name": "Madhanika, small", "selling_price": 48000, "wood_type": "Teak"},
    ).json()
    assert custom["name"] == "Madhanika, small" and D(custom["selling_price"]) == D(
        48000
    )
    assert number(custom["sku"]) == number(made["sku"]) + 1
    linked = client.get(f"/designs/{design['id']}", headers=h).json()["products"]
    assert {x["sku"] for x in linked} == {made["sku"], custom["sku"]}
    stored = client.get(f"/items/{made['id']}", headers=people["owner"]["h"]).json()
    assert (
        stored["item_type"] == "sculpture"
        and stored["description"] == "Lady with the mirror"
    )


def test_design_edit_search_and_validation(client, people):
    h = people["owner"]["h"]
    d = new_design(client, h, f"Unique {uuid.uuid4().hex[:6]}")
    r = client.patch(
        f"/designs/{d['id']}",
        headers=h,
        json={"status": "ready", "description": "Final"},
    )
    assert r.status_code == 200 and r.json()["status"] == "ready"
    assert (
        client.patch(
            f"/designs/{d['id']}", headers=h, json={"status": "done"}
        ).status_code
        == 422
    )
    assert (
        client.patch(f"/designs/{d['id']}", headers=h, json={"name": None}).status_code
        == 422
    )
    assert [
        x["id"]
        for x in client.get(
            f"/designs?search={d['name'][-6:].upper()}", headers=h
        ).json()
    ] == [d["id"]]
    assert d["id"] in [
        x["id"] for x in client.get("/designs?status=ready", headers=h).json()
    ]
    assert client.get(f"/designs/{uuid.uuid4()}", headers=h).status_code == 404
    assert client.post("/designs", headers=h, json={"name": ""}).status_code == 422


@pytest.mark.parametrize("role", OUTSIDERS)
def test_only_the_owner_and_artisans_see_designs(client, people, role):
    owner, h = people["owner"]["h"], people[role]["h"]
    d = new_design(client, owner)
    item = new_item(client, owner, item_type="sculpture")
    assert client.get("/designs", headers=h).status_code == 403
    assert client.get(f"/designs/{d['id']}", headers=h).status_code == 403
    assert client.post("/designs", headers=h, json={"name": "x"}).status_code == 403
    assert (
        client.post(
            f"/designs/{d['id']}/products", headers=h, json={"item_id": item["id"]}
        ).status_code
        == 403
    )
    assert (
        client.post(
            f"/designs/{d['id']}/create-product", headers=h, json={}
        ).status_code
        == 403
    )
    assert client.get("/designs").status_code == 401


# ------------------------------------------------- bought pieces: finishing and painting
def finishing(client, h, item, **extra):
    r = client.post(
        "/restorations",
        headers=h,
        json={
            "kind": "finishing",
            "title": f"Finish {item['name']}",
            "item_id": item["id"],
            **extra,
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def test_a_bought_piece_gets_a_finishing_project(client, people):
    owner, artisan = people["owner"]["h"], people["artisan"]["h"]
    supplier = client.post(
        "/suppliers/",
        headers=owner,
        json={"name": f"Ramu Artisan {uuid.uuid4().hex[:4]}"},
    ).json()
    piece = new_item(
        client, owner, "Bought Ganesha", item_type="sculpture", purchase_price=3000
    )
    p = finishing(
        client,
        artisan,
        piece,
        supplier_id=supplier["id"],
        condition_notes="Rough chisel marks",
    )
    assert (
        p["kind"] == "finishing"
        and p["temple_name"] is None
        and p["status"] == "planned"
    )
    assert (
        p["item_sku"] == piece["sku"]
        and p["item_name"] == "Bought Ganesha"
        and p["supplier_name"] == supplier["name"]
    )
    for stage in ("finishing", "painting", "restored"):
        r = client.patch(
            f"/restorations/{p['id']}", headers=artisan, json={"status": stage}
        )
        assert r.status_code == 200 and r.json()["status"] == stage


def test_finishing_and_temple_projects_have_their_own_rules(client, people):
    owner = people["owner"]["h"]
    piece = new_item(client, owner, "Stool", item_type="sculpture")
    body = {"title": "x"}
    assert (
        client.post(
            "/restorations", headers=owner, json={**body, "kind": "temple"}
        ).status_code
        == 422
    )  # needs a temple
    assert (
        client.post(
            "/restorations", headers=owner, json={**body, "kind": "finishing"}
        ).status_code
        == 422
    )  # needs its piece
    assert (
        client.post(
            "/restorations",
            headers=owner,
            json={**body, "kind": "finishing", "item_id": str(uuid.uuid4())},
        ).status_code
        == 404
    )
    assert (
        client.post(
            "/restorations",
            headers=owner,
            json={
                **body,
                "kind": "finishing",
                "item_id": piece["id"],
                "supplier_id": str(uuid.uuid4()),
            },
        ).status_code
        == 404
    )
    assert (
        client.post(
            "/restorations",
            headers=owner,
            json={**body, "kind": "weird", "temple_name": "T"},
        ).status_code
        == 422
    )
    p = finishing(client, owner, piece)
    assert (
        client.patch(
            f"/restorations/{p['id']}", headers=owner, json={"item_id": None}
        ).status_code
        == 422
    )
    temple = new_project(client, owner)
    assert (
        client.patch(
            f"/restorations/{temple['id']}", headers=owner, json={"temple_name": ""}
        ).status_code
        == 422
    )
    assert temple["kind"] == "temple"  # existing behaviour is unchanged


def test_what_a_piece_has_cost(client, people):
    owner, artisan = people["owner"]["h"], people["artisan"]["h"]
    piece = new_item(
        client,
        owner,
        "Bought Hanuman",
        item_type="sculpture",
        purchase_price=3000,
        selling_price=9000,
    )
    p = finishing(client, artisan, piece)
    nothing = client.get(f"/restorations/{p['id']}/cost", headers=owner).json()
    assert (
        D(nothing["purchase_price"]) == D(3000)
        and D(nothing["total"]) == D(3000)
        and D(nothing["profit_if_sold"]) == D(6000)
    )

    wid = people["worker"]["id"]  # labour paid on this piece
    for kind, qty, rate in (("piece", 1, 500), ("overtime", 2.5, 100)):
        r = client.post(
            "/payroll/work-entries",
            headers=owner,
            json={
                "user_id": wid,
                "work_date": "2026-10-20",
                "kind": kind,
                "description": "Fine finish",
                "quantity": qty,
                "rate": rate,
                "item_id": piece["id"],
            },
        )
        assert r.status_code == 201, r.text
    paint = client.post(
        "/expenses/",
        headers=owner,
        json={
            "spent_on": "2026-10-21",
            "category": "Paint",
            "amount": 400,
            "restoration_id": p["id"],
        },
    )
    assert paint.status_code == 201 and paint.json()["restoration_id"] == p["id"]
    wrong = client.post(
        "/expenses/",
        headers=owner,
        json={
            "spent_on": "2026-10-21",
            "category": "Paint",
            "amount": 999,
            "restoration_id": p["id"],
        },
    ).json()
    client.post(
        f"/expenses/{wrong['id']}/void", headers=owner, json={"reason": "entered twice"}
    )  # a cancelled one is not counted
    client.post(
        "/expenses/",
        headers=owner,
        json={"spent_on": "2026-10-21", "category": "Rent", "amount": 5000},
    )  # unrelated

    cost = client.get(f"/restorations/{p['id']}/cost", headers=owner).json()
    assert (D(cost["purchase_price"]), D(cost["labour"]), D(cost["expenses"])) == (
        D(3000),
        D(750),
        D(400),
    )
    assert (
        D(cost["total"]) == D(4150)
        and D(cost["selling_price"]) == D(9000)
        and D(cost["profit_if_sold"]) == D(4850)
    )
    assert cost["item_sku"] == piece["sku"]


@pytest.mark.parametrize("role", ["artisan", "manager", "sales", "achari", "worker"])
def test_cost_is_for_the_owner_only(client, people, role):
    owner = people["owner"]["h"]
    p = new_project(client, owner)
    assert (
        client.get(
            f"/restorations/{p['id']}/cost", headers=people[role]["h"]
        ).status_code
        == 403
    )
    assert client.get(f"/restorations/{p['id']}/cost", headers=owner).status_code == 200


def test_an_expense_on_a_project_is_the_owners_to_link(client, people):
    owner, manager = people["owner"]["h"], people["manager"]["h"]
    p = new_project(client, owner)
    body = {"spent_on": "2026-10-21", "category": "Polish", "amount": 150}
    assert (
        client.post(
            "/expenses/", headers=manager, json={**body, "restoration_id": p["id"]}
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/expenses/",
            headers=owner,
            json={**body, "restoration_id": str(uuid.uuid4())},
        ).status_code
        == 404
    )
    plain = client.post(
        "/expenses/", headers=manager, json=body
    )  # a manager still records ordinary expenses
    assert plain.status_code == 201 and plain.json()["restoration_id"] is None
    e = client.post(
        "/expenses/", headers=owner, json={**body, "restoration_id": p["id"]}
    ).json()
    assert [
        x["id"]
        for x in client.get(
            f"/expenses/?restoration_id={p['id']}", headers=owner
        ).json()
    ] == [e["id"]]
    assert (
        client.patch(
            f"/expenses/{e['id']}", headers=manager, json={"restoration_id": None}
        ).status_code
        == 403
    )  # not theirs to change either


# ------------------------------------------------------- sculpture scans are the artisans'
def test_a_sculpture_scan_belongs_to_the_owner_and_artisans(client, people, store):
    owner, artisan, manager = (
        people["owner"]["h"],
        people["artisan"]["h"],
        people["manager"]["h"],
    )
    carving = new_item(client, owner, "Madhanika", item_type="sculpture")
    chair = new_item(client, owner, "Armchair")  # furniture: unchanged behaviour
    for role in ("sales", "achari", "worker", "manager"):
        r = client.post(
            "/scans",
            headers=people[role]["h"],
            json={"item_id": carving["id"], "source_kind": "photos"},
        )
        assert r.status_code == 403, role
    scan = queue(client, artisan, store, item_id=carving["id"])
    furniture_scan = queue(client, people["achari"]["h"], store, item_id=chair["id"])
    assert scan["id"] in [
        s["id"] for s in client.get("/scans?status=uploaded", headers=owner).json()
    ]
    queue_ids = [
        s["id"] for s in client.get("/scans?status=uploaded", headers=manager).json()
    ]
    assert (
        furniture_scan["id"] in queue_ids and scan["id"] not in queue_ids
    )  # a manager runs furniture, not carvings
    assert client.get(f"/scans/{scan['id']}", headers=manager).status_code == 404
    assert (
        client.get(f"/scans/{scan['id']}", headers=people["achari"]["h"]).status_code
        == 404
    )
    other_artisan = artisan
    assert client.get(f"/scans/{scan['id']}", headers=other_artisan).status_code == 200


def test_an_artisan_can_run_a_sculpture_scan_but_not_publish_it(client, people, store):
    owner, artisan = people["owner"]["h"], people["artisan"]["h"]
    carving = new_item(client, owner, "Madhanika", item_type="sculpture")
    scan = queue(client, artisan, store, item_id=carving["id"])
    assert (
        claim(client, artisan, scan["id"]).status_code == 200
    )  # the workshop PC can be an artisan account
    glb = put_result(
        client, artisan, store, scan["id"], "glb", "m.glb", b"glTF" + b"x" * 8
    )
    public = client.post(
        f"/scans/{scan['id']}/finish",
        headers=artisan,
        json={
            "worker": PC,
            "assets": [
                {
                    "key": glb,
                    "kind": "glb",
                    "visibility": "public",
                    "content_type": "model/gltf-binary",
                }
            ],
        },
    )
    assert public.status_code == 403 and "owner or a manager" in public.json()["detail"]
    ok = client.post(
        f"/scans/{scan['id']}/finish",
        headers=artisan,
        json={
            "worker": PC,
            "assets": [
                {
                    "key": glb,
                    "kind": "glb",
                    "visibility": "staff",
                    "content_type": "model/gltf-binary",
                }
            ],
        },
    )
    assert ok.status_code == 200 and ok.json()["status"] == "done"
    # a furniture scan still cannot be run by an artisan
    chair = new_item(client, owner, "Armchair")
    furniture_scan = queue(client, owner, store, item_id=chair["id"])
    assert claim(client, artisan, furniture_scan["id"]).status_code == 404


def test_an_artisan_can_list_a_sculptures_files_but_not_furniture(client, people):
    owner = people["owner"]["h"]
    carving = new_item(client, owner, "Madhanika", item_type="sculpture")
    chair = new_item(client, owner, "Armchair")
    assert (
        client.get(
            f"/items/{carving['id']}/assets", headers=people["artisan"]["h"]
        ).status_code
        == 200
    )
    assert (
        client.get(
            f"/items/{chair['id']}/assets", headers=people["artisan"]["h"]
        ).status_code
        == 404
    )
    assert (
        client.get(
            f"/items/{uuid.uuid4()}/assets", headers=people["artisan"]["h"]
        ).status_code
        == 404
    )
    for role in ("sales", "achari", "worker"):
        assert (
            client.get(
                f"/items/{carving['id']}/assets", headers=people[role]["h"]
            ).status_code
            == 403
        )
    for item in (carving, chair):  # owner and manager keep seeing every product's files
        for role in ("owner", "manager"):
            assert (
                client.get(
                    f"/items/{item['id']}/assets", headers=people[role]["h"]
                ).status_code
                == 200
            )
