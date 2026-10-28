"""Item type (furniture or sculpture) and automatic SKUs (FUR-0001, SCU-0001)."""

import re
import uuid
from datetime import datetime, timezone

import pytest


def number(sku):
    return int(sku.split("-")[1])


def make(client, h, name="Piece", **extra):
    r = client.post(
        "/items/", headers=h, json={"name": name, "selling_price": 1000, **extra}
    )
    return r


def test_the_server_gives_the_next_code_for_each_type(client, people):
    h = people["owner"]["h"]
    f1 = make(client, h, item_type="furniture")
    s1 = make(client, h, item_type="sculpture")
    assert f1.status_code == 200 and s1.status_code == 200, (f1.text, s1.text)
    f2 = make(client, h, "Second chair")
    s2 = make(client, h, "Second carving", item_type="sculpture")
    assert re.fullmatch(r"FUR-\d{4,}", f1.json()["sku"]) and re.fullmatch(
        r"SCU-\d{4,}", s1.json()["sku"]
    )
    assert (
        f1.json()["item_type"] == "furniture" and s1.json()["item_type"] == "sculpture"
    )
    assert (
        number(f2.json()["sku"]) == number(f1.json()["sku"]) + 1
    )  # furniture is the default type
    assert (
        number(s2.json()["sku"]) == number(s1.json()["sku"]) + 1
    )  # each type counts on its own


def test_many_in_a_row_never_repeat(client, people):
    h = people["owner"]["h"]
    skus = [make(client, h, f"Stool {i}").json()["sku"] for i in range(25)]
    assert len(set(skus)) == 25
    assert [number(s) for s in skus] == list(
        range(number(skus[0]), number(skus[0]) + 25)
    )


@pytest.mark.parametrize("blank", ["", "   ", None])
def test_a_blank_sku_means_automatic(client, people, blank):
    r = make(client, people["owner"]["h"], sku=blank)
    assert r.status_code == 200 and r.json()["sku"].startswith("FUR-")


def test_a_typed_code_is_kept_and_a_clash_is_refused(client, people):
    h = people["owner"]["h"]
    mine = f"OLD-{uuid.uuid4().hex[:6]}"
    r = make(client, h, sku=f"  {mine}  ")
    assert (
        r.status_code == 200 and r.json()["sku"] == mine
    )  # trimmed, otherwise untouched
    again = make(client, h, sku=mine)
    assert again.status_code in (400, 409) and again.status_code != 500


def test_a_hand_typed_code_is_skipped_not_reused(client, people):
    h = people["owner"]["h"]
    n = number(make(client, h).json()["sku"])
    taken = f"FUR-{n + 1:04d}"
    assert (
        make(client, h, sku=taken).json()["sku"] == taken
    )  # someone typed the next number by hand
    nxt = make(client, h).json()["sku"]
    assert number(nxt) == n + 2 and nxt != taken


def test_type_is_checked_and_can_change_without_changing_the_code(client, people):
    h = people["owner"]["h"]
    assert make(client, h, item_type="statue").status_code == 422
    item = make(client, h, "Temple Ganesha").json()
    sku = item["sku"]
    r = client.put(f"/items/{item['id']}", headers=h, json={"item_type": "sculpture"})
    assert r.status_code == 200, r.text
    assert (
        r.json()["item_type"] == "sculpture" and r.json()["sku"] == sku
    )  # a code never changes by itself


def test_the_list_can_be_filtered_by_type(client, people):
    h = people["owner"]["h"]
    a = make(client, h, "Filter chair").json()
    b = make(client, h, "Filter carving", item_type="sculpture").json()

    def ids(kind):
        return {
            i["id"]
            for i in client.get(f"/items/?limit=100&item_type={kind}", headers=h).json()
        }

    assert a["id"] in ids("furniture") and b["id"] not in ids("furniture")
    assert b["id"] in ids("sculpture") and a["id"] not in ids("sculpture")
    assert client.get("/items/?item_type=statue", headers=h).status_code == 422


def test_paging_through_items_is_stable(client, people):
    h = people["owner"]["h"]
    for i in range(5):
        make(client, h, f"Page {i}")
    seen = []
    for offset in range(0, 100000, 2):
        page = client.get(f"/items/?limit=2&offset={offset}", headers=h).json()
        seen += [i["id"] for i in page]
        if len(page) < 2 or offset > 400:
            break
    assert len(seen) == len(set(seen))  # no item repeated across pages


@pytest.mark.parametrize("role", ["sales", "achari", "worker", "artisan"])
def test_only_owner_and_manager_create_items(client, people, role):
    assert make(client, people[role]["h"]).status_code == 403


def test_an_item_made_offline_without_a_code_gets_one_when_it_syncs(auth_client):
    item_id = str(uuid.uuid4())
    op = {
        "id": str(uuid.uuid4()),
        "entity": "item",
        "action": "create",
        "payload": {"id": item_id, "name": "Offline carving", "item_type": "sculpture"},
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    r = auth_client.post("/sync/push", json={"operations": [op]})
    assert r.status_code == 200 and not r.json()["failed"], r.text
    made = auth_client.get(f"/items/{item_id}").json()
    assert made["sku"].startswith("SCU-") and made["item_type"] == "sculpture"


def test_the_website_knows_which_pieces_are_sculptures(client, people):
    h = people["owner"]["h"]
    r = make(
        client,
        h,
        "Hoysala Madhanika",
        item_type="sculpture",
        is_published=True,
        selling_price=48000,
        wood_type="Teak",
    )
    assert r.status_code == 200, r.text
    slug = r.json()["slug"]
    shown = client.get(f"/public/catalog/{slug}")
    assert shown.status_code == 200 and shown.json()["item_type"] == "sculpture"
    assert (
        "sku" not in shown.text.lower() or "FUR-" not in shown.text
    )  # internal codes stay off the site
