"""
One pricing rule everywhere: cost = buying price + making cost; selling price (before GST)
= cost + profit %; the customer pays selling price + GST. Items, billing and the website
must agree on it.
"""

from decimal import Decimal
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.services import pricing
from tests.test_public_api import checkout_body, make_item


def D(x):
    return Decimal(str(x))


def new(client, h, **extra):
    r = client.post("/items/", headers=h, json={"name": "Armchair", **extra})
    assert r.status_code == 200, r.text
    return r.json()


# ------------------------------------------------------------- the formula
def test_the_formula_steps():
    cost = pricing.cost_of(9000, 7000)
    assert cost == D("16000.00")
    net = pricing.price_from_cost(cost, 25)
    assert net == D("20000.00")
    assert pricing.with_gst(net, 12) == D("22400.00")
    assert pricing.with_gst(net, 0) == D("20000.00")
    assert pricing.net_from_gross(D("22400"), 12) == D("20000.00")
    assert pricing.profit_percent_of(16000, 20000) == D("25.00")
    assert pricing.profit_percent_of(0, 20000) is None


def test_rounding_is_to_the_paisa_half_up():
    assert pricing.price_from_cost("333.33", 10) == D("366.66")
    assert pricing.with_gst("100.05", 5) == D("105.05")
    assert pricing.with_gst(None, None) == D("0.00")


def test_reprice_only_touches_items_priced_by_profit():
    typed = SimpleNamespace(purchase_price=100, production_cost=50, profit_percent=None, selling_price=999)  # fmt: skip
    pricing.reprice(typed)
    assert typed.selling_price == 999  # a typed price is left alone
    formula = SimpleNamespace(purchase_price=100, production_cost=50, profit_percent=20, selling_price=0)  # fmt: skip
    pricing.reprice(formula)
    assert formula.selling_price == D("180.00")


# --------------------------------------------------------------- the items
def test_an_item_priced_by_profit_gets_its_selling_price_and_price_with_gst(
    client, people
):
    h = people["owner"]["h"]
    item = new(client, h, purchase_price=9000, production_cost=7000, profit_percent=25, gst_percent=12)  # fmt: skip
    assert D(item["selling_price"]) == D("20000.00")
    assert D(item["price_with_gst"]) == D("22400.00") and D(item["profit_percent"]) == D("25.00")  # fmt: skip


def test_a_typed_price_wins_and_has_no_profit_percent(client, people):
    h = people["owner"]["h"]
    item = new(client, h, purchase_price=100, production_cost=50, selling_price=300, gst_percent=5)  # fmt: skip
    assert item["profit_percent"] is None and D(item["selling_price"]) == D("300.00")
    assert D(item["price_with_gst"]) == D("315.00")


def test_changing_the_cost_or_the_profit_moves_the_price(client, people):
    h = people["owner"]["h"]
    item = new(client, h, purchase_price=1000, production_cost=500, profit_percent=20)
    assert D(item["selling_price"]) == D("1800.00")
    r = client.put(f"/items/{item['id']}", headers=h, json={"production_cost": 1000})
    assert D(r.json()["selling_price"]) == D("2400.00")
    r = client.put(f"/items/{item['id']}", headers=h, json={"profit_percent": 50})
    assert D(r.json()["selling_price"]) == D("3000.00")
    # typing a price in the same request keeps that price
    r = client.put(f"/items/{item['id']}", headers=h, json={"purchase_price": 2000, "selling_price": 5000})  # fmt: skip
    assert D(r.json()["selling_price"]) == D("5000.00")
    # and an item with no profit % is never repriced
    plain = new(client, h, purchase_price=100, selling_price=500)
    r = client.put(f"/items/{plain['id']}", headers=h, json={"purchase_price": 900})
    assert D(r.json()["selling_price"]) == D("500.00")


def test_the_profit_percent_is_bounded(client, people):
    h = people["owner"]["h"]
    for bad in (-1, 1001):
        r = client.post("/items/", headers=h, json={"name": "x", "profit_percent": bad})
        assert r.status_code == 422


def test_only_owner_and_manager_see_the_profit_percent(client, people):
    item = new(client, people["owner"]["h"], purchase_price=100, profit_percent=40, gst_percent=5)  # fmt: skip
    for role in ("owner", "manager"):
        got = client.get(f"/items/{item['id']}", headers=people[role]["h"]).json()
        assert D(got["profit_percent"]) == D("40.00"), role
    for role in ("sales", "worker", "artisan", "achari"):
        got = client.get(f"/items/{item['id']}", headers=people[role]["h"]).json()
        assert got["profit_percent"] is None and D(got["purchase_price"]) == 0, role
        assert D(got["price_with_gst"]) == D(
            "147.00"
        ), role  # the price itself is not secret


# ------------------------------------------------ billing and the website agree
def test_a_bill_charges_exactly_the_price_with_gst(client, people):
    h = people["owner"]["h"]
    item = new(client, h, purchase_price=9000, production_cost=7000, profit_percent=25, gst_percent=12, current_stock=5)  # fmt: skip
    r = client.post("/sales/", headers=h, json={"items": [{"item_id": item["id"], "quantity": 2}]})  # fmt: skip
    assert r.status_code == 200, r.text
    sale = r.json()
    assert D(sale["items"][0]["total_price"]) == D(item["price_with_gst"]) * 2
    assert D(sale["total_amount"]) == D("44800.00") and D(sale["tax_total"]) == D("4800.00")  # fmt: skip


def test_the_website_shows_and_charges_the_price_with_gst(auth_client: TestClient):
    item = make_item(auth_client, selling_price=1000, gst_percent=18)
    slug = item["slug"]
    shown = auth_client.get(f"/public/catalog/{slug}").json()
    assert D(shown["price"]) == D("1180.00")
    r = auth_client.post("/public/checkout", json=checkout_body(slug, 2))
    assert r.status_code in (200, 201), r.text
    assert D(r.json()["total"]) == D("2360.00")
