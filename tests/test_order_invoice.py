"""Billing an order: one bill per order, at the prices agreed, with stock, payments and GST right."""

import uuid
from decimal import Decimal

from tests.test_order_processing import new_customer, new_order, pay


def D(x):
    return Decimal(str(x))


def confirm(client, h, order):
    r = client.patch(
        f"/orders/{order['id']}/status", headers=h, json={"status": "confirmed"}
    )
    assert r.status_code == 200, r.text
    return r.json()


def new_item(client, h, **extra):
    body = {
        "name": f"Teak stool {uuid.uuid4().hex[:5]}",
        "selling_price": 1000,
        "current_stock": 5,
        **extra,
    }
    r = client.post("/items/", headers=h, json=body)
    assert r.status_code == 200, r.text
    return r.json()


def catalogue_order(client, h, lines, estimate, customer=True, **extra):
    """lines: [(item, qty, unit_price_with_gst)]"""
    body = {
        "order_type": "standard",
        "items": [
            {"item_id": i["id"], "quantity": q, "unit_price": p} for i, q, p in lines
        ],
        "estimated_amount": estimate,
        **extra,
    }
    if customer:
        body["customer_id"] = new_customer(client, h)["id"]
    r = client.post("/orders/", headers=h, json=body)
    assert r.status_code == 201, r.text
    return r.json()


def bill(client, h, order, **body):
    return client.post(f"/orders/{order['id']}/invoice", headers=h, json=body)


def stock(client, h, item):
    return client.get(f"/items/{item['id']}", headers=h).json()["current_stock"]


# ---------------------------------------------------------------- a custom piece
def test_a_custom_order_becomes_a_bill_with_its_own_words(client, people):
    h = people["owner"]["h"]
    customer = new_customer(client, h, name="Lakshmi Devi")
    order = confirm(client, h, new_order(client, h, customer=customer, estimate=10000))
    r = bill(client, h, order)
    assert r.status_code == 201, r.text
    sale = r.json()
    assert sale["invoice_number"].startswith("NTD-") and D(sale["total_amount"]) == D(
        "10000"
    )
    assert (
        D(sale["tax_total"]) == 0
        and D(sale["discount"]) == 0
        and sale["customer_id"] == customer["id"]
    )
    line = sale["items"][0]
    assert line["item_name"] == "Carved temple door" and line["item_id"] is None
    assert D(line["price_at_sale"]) == D("10000") and D(line["total_price"]) == D(
        "10000"
    )
    # the order now knows its bill
    got = client.get(f"/orders/{order['id']}", headers=h).json()
    assert (
        got["sale_id"] == sale["id"] and got["invoice_number"] == sale["invoice_number"]
    )
    assert any(o["invoice_number"] == sale["invoice_number"] for o in client.get("/orders/", headers=h, params={"search": customer["name"]}).json())  # fmt: skip
    # and the bill is in the ordinary bills list
    assert client.get(f"/sales/{sale['id']}", headers=h).status_code == 200


def test_asking_twice_gives_the_same_bill_and_takes_stock_once(client, people):
    h = people["owner"]["h"]
    item = new_item(client, h, current_stock=5)
    order = confirm(client, h, catalogue_order(client, h, [(item, 2, 1000)], 2000))
    first, second = bill(client, h, order), bill(client, h, order)
    assert (first.status_code, second.status_code) == (201, 200)
    assert first.json()["invoice_number"] == second.json()["invoice_number"]
    assert stock(client, h, item) == 3  # 5 - 2, not 5 - 4


# ---------------------------------------------------------------------- the money
def test_gst_is_taken_back_out_so_the_bill_agrees_with_every_other_bill(client, people):
    h = people["owner"]["h"]
    item = new_item(client, h, selling_price=1000, gst_percent=12, current_stock=5)
    order = confirm(
        client, h, catalogue_order(client, h, [(item, 2, 1120)], 2240)
    )  # 1120 = 1000 + 12%
    sale = bill(client, h, order).json()
    assert (D(sale["sub_total"]), D(sale["tax_total"]), D(sale["total_amount"])) == (
        D("2000"),
        D("240"),
        D("2240"),
    )
    line = sale["items"][0]
    assert D(line["price_at_sale"]) == D("1000") and D(line["gst_percent"]) == 12
    assert (D(line["cgst_amount"]), D(line["sgst_amount"]), D(line["total_price"])) == (
        D("120"),
        D("120"),
        D("2240"),
    )
    assert item["id"] == line["item_id"] and stock(client, h, item) == 3


def test_the_price_agreed_on_the_order_wins_over_the_catalogue_price(client, people):
    h = people["owner"]["h"]
    item = new_item(client, h, selling_price=1000, current_stock=5)
    order = confirm(client, h, catalogue_order(client, h, [(item, 1, 900)], 900))
    sale = bill(client, h, order).json()
    assert D(sale["items"][0]["price_at_sale"]) == D("900") and D(
        sale["total_amount"]
    ) == D("900")


def test_a_lower_agreed_total_is_a_discount_and_a_higher_one_adds_other_charges(
    client, people
):
    h = people["owner"]["h"]
    item = new_item(client, h, current_stock=9)
    cheaper = confirm(client, h, catalogue_order(client, h, [(item, 2, 1000)], 1800))
    sale = bill(client, h, cheaper).json()
    assert D(sale["discount"]) == D("200") and D(sale["total_amount"]) == D("1800")
    dearer = confirm(client, h, catalogue_order(client, h, [(item, 2, 1000)], 2500))
    sale = bill(client, h, dearer).json()
    assert D(sale["total_amount"]) == D("2500") and D(sale["discount"]) == 0
    assert "Other charges" in [x["item_name"] for x in sale["items"]]
    other = next(x for x in sale["items"] if x["item_name"] == "Other charges")
    assert D(other["total_price"]) == D("500") and other["item_id"] is None


def test_the_final_amount_is_what_gets_billed_once_it_is_set(client, people):
    h = people["owner"]["h"]
    order = confirm(
        client,
        h,
        new_order(client, h, customer=new_customer(client, h), estimate=10000),
    )
    assert (
        client.patch(
            f"/orders/{order['id']}", headers=h, json={"final_amount": 9000}
        ).status_code
        == 200
    )
    assert D(bill(client, h, order).json()["total_amount"]) == D("9000")


# ------------------------------------------------------------------- the payments
def test_payments_so_far_show_on_the_bill_and_follow_later_ones(
    client, people, monkeypatch
):
    h = people["owner"]["h"]
    order = confirm(
        client,
        h,
        new_order(client, h, customer=new_customer(client, h), estimate=10000),
    )
    assert (
        pay(client, h, order, 3000, method="upi", reference="UPI1").status_code == 201
    )
    sale = bill(client, h, order).json()
    assert sale["payment_status"] == "partial" and sale["payment_method"] == "upi"
    assert pay(client, h, order, 7000, method="cash").status_code == 201
    again = client.get(f"/sales/{sale['id']}", headers=h).json()
    assert again["payment_status"] == "paid" and again["payment_method"] == "cash"


def test_an_unpaid_order_is_billed_as_pending(client, people):
    h = people["owner"]["h"]
    order = confirm(
        client, h, new_order(client, h, customer=new_customer(client, h), estimate=500)
    )
    assert bill(client, h, order).json()["payment_status"] == "pending"


def test_the_bill_pdf_names_the_order_and_what_is_still_to_pay(
    client, people, monkeypatch
):
    from reportlab import rl_config

    monkeypatch.setattr(rl_config, "pageCompression", 0)
    h = people["owner"]["h"]
    order = confirm(
        client,
        h,
        new_order(
            client, h, customer=new_customer(client, h), estimate=10000, advance=0
        ),
    )
    pay(client, h, order, 2500)
    sale = bill(client, h, order).json()
    r = client.get(f"/invoices/{sale['invoice_number']}/pdf", headers=h)
    assert r.status_code == 200 and r.content.startswith(b"%PDF")
    text = r.content.decode("latin-1")
    for fragment in (
        order["order_number"],
        "Received so far",
        "Rs. 2500.00",
        "Balance due",
        "Rs. 7500.00",
        "Carved temple door",
        "Grand Total",
    ):
        assert fragment in text, fragment


def test_a_discounted_bill_pdf_shows_the_discount_and_the_real_total(
    client, people, monkeypatch
):
    from reportlab import rl_config

    monkeypatch.setattr(rl_config, "pageCompression", 0)
    h = people["owner"]["h"]
    item = new_item(client, h, current_stock=9)
    order = confirm(client, h, catalogue_order(client, h, [(item, 2, 1000)], 1800))
    sale = bill(client, h, order).json()
    text = client.get(
        f"/invoices/{sale['invoice_number']}/pdf", headers=h
    ).content.decode("latin-1")
    assert "Discount" in text and "Rs. 200.00" in text and "Rs. 1800.00" in text


# ------------------------------------------------------------------- the rules
def test_an_order_that_is_not_ready_to_bill_is_refused_with_the_reason(client, people):
    h = people["owner"]["h"]
    draft = new_order(client, h, customer=new_customer(client, h), estimate=500)
    r = bill(client, h, draft)
    assert r.status_code == 409 and "Confirm the order" in r.json()["detail"]
    cancelled = new_order(client, h, customer=new_customer(client, h), estimate=500)
    client.patch(
        f"/orders/{cancelled['id']}/status", headers=h, json={"status": "cancelled"}
    )
    assert "cancelled" in bill(client, h, cancelled).json()["detail"]
    no_amount = confirm(
        client, h, new_order(client, h, customer=new_customer(client, h), estimate=0)
    )
    assert "amount" in bill(client, h, no_amount).json()["detail"]
    assert bill(client, h, {"id": str(uuid.uuid4())}).status_code == 404


def test_not_enough_stock_stops_the_bill_unless_the_goods_already_left(client, people):
    h = people["owner"]["h"]
    item = new_item(client, h, current_stock=1)
    order = confirm(client, h, catalogue_order(client, h, [(item, 3, 1000)], 3000))
    r = bill(client, h, order)
    assert r.status_code == 400 and "stock" in r.json()["detail"].lower()
    assert stock(client, h, item) == 1  # nothing was taken, no bill, no number used
    assert client.get(f"/orders/{order['id']}", headers=h).json()["sale_id"] is None
    ok = bill(client, h, order, allow_negative_stock=True)
    assert ok.status_code == 201 and stock(client, h, item) == -2


def test_a_billed_order_cannot_change_its_amount_or_be_cancelled(client, people):
    h = people["owner"]["h"]
    order = confirm(
        client,
        h,
        new_order(client, h, customer=new_customer(client, h), estimate=10000),
    )
    sale = bill(client, h, order).json()
    r = client.patch(f"/orders/{order['id']}", headers=h, json={"final_amount": 5000})
    assert r.status_code == 409 and sale["invoice_number"] in r.json()["detail"]
    r = client.patch(
        f"/orders/{order['id']}/status", headers=h, json={"status": "cancelled"}
    )
    assert r.status_code == 409 and "Cancel the bill first" in r.json()["detail"]
    # what does not touch the money is still fine
    assert (
        client.patch(
            f"/orders/{order['id']}", headers=h, json={"notes": "Polish after delivery"}
        ).status_code
        == 200
    )
    assert (
        client.patch(
            f"/orders/{order['id']}/status", headers=h, json={"status": "in_production"}
        ).status_code
        == 200
    )


def test_who_may_bill_an_order(client, people):
    h = people["owner"]["h"]
    order = confirm(
        client, h, new_order(client, h, customer=new_customer(client, h), estimate=500)
    )
    for role in ("worker", "achari", "artisan"):
        assert bill(client, people[role]["h"], order).status_code == 403, role
    assert (
        bill(client, people["sales"]["h"], order).status_code == 201
    )  # sales takes orders and bills them
    assert (
        bill(client, people["manager"]["h"], order).status_code == 200
    )  # same bill again
    assert client.post(f"/orders/{order['id']}/invoice", json={}).status_code == 401


def test_the_order_is_billed_with_no_body_at_all(client, people):
    h = people["owner"]["h"]
    order = confirm(
        client, h, new_order(client, h, customer=new_customer(client, h), estimate=500)
    )
    r = client.post(
        f"/orders/{order['id']}/invoice", headers=h
    )  # what a simple client sends
    assert r.status_code == 201, r.text
