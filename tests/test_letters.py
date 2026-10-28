"""
Official letters: quotation, order confirmation with advance receipt, purchase receipt and
the completion certificate; numbers issued once, snapshots, voiding, the letterhead and who
may see what.
"""

import re
import uuid
from datetime import date
from decimal import Decimal

from reportlab import rl_config

from app.services.letter_pdf_service import amount_in_words, money
from app.services.letter_service import financial_year
from tests.test_order_processing import new_customer, new_order, pay
from tests.test_restoration import new_project

LINES = [
    {"description": "Carved temple door", "quantity": 1, "unit_price": "45000"},
    {"description": "Door frame", "quantity": 2, "unit_price": "6250.50"},
]


def pdf_text(client, h, letter, monkeypatch):
    """The PDF with uncompressed streams, so its text can be searched."""
    monkeypatch.setattr(rl_config, "pageCompression", 0)
    r = client.get(f"/letters/{letter['id']}/pdf", headers=h)
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf"
    assert r.content.startswith(b"%PDF")
    return r.content.decode("latin-1")


def quotation(client, h, **extra):
    r = client.post(
        "/letters/quotation",
        headers=h,
        json={"party_name": "Sri Rama Temple Trust", "lines": LINES, **extra},
    )
    assert r.status_code == 201, r.text
    return r.json()


def new_supplier(client, h, name="Mohan the carver"):
    r = client.post(
        "/suppliers/", headers=h, json={"name": name, "phone": "9000011111"}
    )
    assert r.status_code in (200, 201), r.text
    return r.json()


def restored_project(client, h, **extra):
    p = new_project(client, h, **extra)
    r = client.patch(f"/restorations/{p['id']}", headers=h, json={"status": "restored"})
    assert r.status_code == 200, r.text
    return p


# ------------------------------------------------------------ words and money
def test_amounts_in_words_use_indian_grouping():
    assert amount_in_words(0) == "Rupees Zero Only"
    assert amount_in_words(1250.5) == "Rupees One Thousand Two Hundred Fifty and Fifty Paise Only"  # fmt: skip
    assert amount_in_words(1234567) == "Rupees Twelve Lakh Thirty Four Thousand Five Hundred Sixty Seven Only"  # fmt: skip
    assert amount_in_words(25000000) == "Rupees Two Crore Fifty Lakh Only"
    assert money(123456.5) == "Rs. 1,23,456.50" and money(999) == "Rs. 999.00"


def test_financial_year_starts_in_april():
    assert financial_year(date(2026, 3, 31)) == "2025-26"
    assert financial_year(date(2026, 4, 1)) == "2026-27"
    assert financial_year(date(2099, 12, 1)) == "2099-00"


# ---------------------------------------------------------------- quotation
def test_a_quotation_is_numbered_totalled_and_drawn_on_the_letterhead(
    client, people, monkeypatch
):
    h = people["sales"]["h"]
    q = quotation(client, h, notes="Teak wood, polished")
    assert re.fullmatch(r"QTN-\d{4}-\d{2}-\d{4}", q["number"])
    assert q["status"] == "issued" and q["amount"] == "57501.00"
    detail = client.get(f"/letters/{q['id']}", headers=h).json()
    p = detail["payload"]
    assert p["total"] == "57501.00" and p["lines"][1]["amount"] == "12501.00"
    assert p["letterhead"]["name"] == "Vriksha Studios" and p["terms"]
    text = pdf_text(client, h, q, monkeypatch)
    for fragment in (q["number"], "QUOTATION", "Sri Rama Temple Trust", "Rs. 57,501.00", "Fifty Seven Thousand Five Hundred One", "Vriksha Studios"):  # fmt: skip
        assert fragment in text, fragment


def test_numbers_count_up_and_are_never_reused(client, people):
    h = people["owner"]["h"]
    a, b = quotation(client, h), quotation(client, h)
    na, nb = (int(x["number"].rsplit("-", 1)[1]) for x in (a, b))
    assert nb == na + 1 and a["number"].rsplit("-", 1)[0] == b["number"].rsplit("-", 1)[0]  # fmt: skip
    client.post(f"/letters/{b['id']}/void", headers=h, json={"reason": "typo in rate"})
    c = quotation(client, h)
    assert int(c["number"].rsplit("-", 1)[1]) == nb + 1


def test_a_quotation_can_take_its_lines_and_customer_from_an_order(client, people):
    h = people["owner"]["h"]
    customer = new_customer(client, h, name="Lakshmi Devi")
    order = new_order(client, h, customer=customer, estimate=18000)
    r = client.post("/letters/quotation", headers=h, json={"order_id": order["id"]})
    assert r.status_code == 201, r.text
    q = r.json()
    assert q["party_name"] == "Lakshmi Devi" and q["amount"] == "18000.00"
    assert q["order_id"] == order["id"]


def test_a_quotation_needs_a_party_and_lines(client, people):
    h = people["owner"]["h"]
    assert client.post("/letters/quotation", headers=h, json={"lines": LINES}).status_code == 422  # fmt: skip
    assert client.post("/letters/quotation", headers=h, json={"party_name": "X"}).status_code == 422  # fmt: skip
    bad = {"party_name": "X", "lines": [{"description": "d", "quantity": 0, "unit_price": 1}]}  # fmt: skip
    assert client.post("/letters/quotation", headers=h, json=bad).status_code == 422


# -------------------------------------------------------- order confirmation
def test_an_order_confirmation_carries_the_advance_and_balance(
    client, people, monkeypatch
):
    h = people["sales"]["h"]
    customer = new_customer(client, h, name="Venkat Rao")
    order = new_order(client, h, customer=customer, estimate=20000)
    assert pay(client, h, order, 5000, method="upi", reference="UPI123").status_code == 201  # fmt: skip
    r = client.post("/letters/order-confirmation", headers=h, json={"order_id": order["id"]})  # fmt: skip
    assert r.status_code == 201, r.text
    letter = r.json()
    assert letter["number"].startswith("OCF-") and letter["amount"] == "5000.00"
    p = client.get(f"/letters/{letter['id']}", headers=h).json()["payload"]
    assert (p["total"], p["advance"], p["balance"]) == ("20000.00", "5000.00", "15000.00")  # fmt: skip
    assert p["payments"][0]["reference"] == "UPI123"
    text = pdf_text(client, h, letter, monkeypatch)
    for fragment in ("Venkat Rao", order["order_number"], "Rs. 15,000.00", "Five Thousand", "UPI123"):  # fmt: skip
        assert fragment in text, fragment


def test_a_letter_keeps_what_it_said_when_the_order_changes_later(client, people):
    h = people["owner"]["h"]
    order = new_order(client, h, customer=new_customer(client, h), estimate=20000)
    pay(client, h, order, 2000)
    letter = client.post("/letters/order-confirmation", headers=h, json={"order_id": order["id"]}).json()  # fmt: skip
    pay(client, h, order, 8000)
    again = client.get(f"/letters/{letter['id']}", headers=h).json()
    assert again["payload"]["advance"] == "2000.00" and again["amount"] == "2000.00"


def test_an_order_confirmation_needs_a_customer_and_a_live_order(client, people):
    h = people["owner"]["h"]
    no_customer = new_order(client, h)
    r = client.post("/letters/order-confirmation", headers=h, json={"order_id": no_customer["id"]})  # fmt: skip
    assert r.status_code == 422
    order = new_order(client, h, customer=new_customer(client, h))
    client.patch(f"/orders/{order['id']}/status", headers=h, json={"status": "cancelled"})  # fmt: skip
    r = client.post("/letters/order-confirmation", headers=h, json={"order_id": order["id"]})  # fmt: skip
    assert r.status_code == 409
    missing = {"order_id": str(uuid.uuid4())}
    assert client.post("/letters/order-confirmation", headers=h, json=missing).status_code == 404  # fmt: skip


# ----------------------------------------------------------- purchase receipt
def test_a_purchase_receipt_names_the_seller_and_amount(client, people, monkeypatch):
    h = people["manager"]["h"]
    supplier = new_supplier(client, people["owner"]["h"])
    body = {
        "supplier_id": supplier["id"],
        "description": "Carved Ganesha, 2 ft, unfinished",
        "amount": "7500",
        "method": "upi",
        "reference": "TXN987",
    }
    r = client.post("/letters/purchase-receipt", headers=h, json=body)
    assert r.status_code == 201, r.text
    letter = r.json()
    assert letter["number"].startswith("PRC-") and letter["party_name"] == supplier["name"]  # fmt: skip
    assert letter["supplier_id"] == supplier["id"] and letter["amount"] == "7500.00"
    text = pdf_text(client, h, letter, monkeypatch)
    for fragment in (supplier["name"], "Carved Ganesha", "Rs. 7,500.00", "Seven Thousand Five Hundred", "TXN987", "Seller"):  # fmt: skip
        assert fragment in text, fragment


def test_a_purchase_receipt_can_name_a_seller_without_a_supplier_record(client, people):  # fmt: skip
    h = people["owner"]["h"]
    r = client.post("/letters/purchase-receipt", headers=h, json={"seller_name": "Ravi", "description": "Peacock panel", "amount": 900})  # fmt: skip
    assert r.status_code == 201 and r.json()["supplier_id"] is None
    nobody = client.post("/letters/purchase-receipt", headers=h, json={"description": "x", "amount": 5})  # fmt: skip
    assert nobody.status_code == 422
    zero = client.post("/letters/purchase-receipt", headers=h, json={"seller_name": "R", "description": "x", "amount": 0})  # fmt: skip
    assert zero.status_code == 422


# ----------------------------------------------------- completion certificate
def test_a_certificate_needs_a_restored_temple_project(client, people, monkeypatch):
    h = people["owner"]["h"]
    open_project = new_project(client, h)
    r = client.post("/letters/completion-certificate", headers=h, json={"restoration_id": open_project["id"], "work_done": "Recarved"})  # fmt: skip
    assert r.status_code == 409
    done = restored_project(client, h, temple_name="Sri Veerabhadra Temple")
    r = client.post(
        "/letters/completion-certificate",
        headers=h,
        json={"restoration_id": done["id"], "work_done": "Nose and left hand recarved in teak."},  # fmt: skip
    )
    assert r.status_code == 201, r.text
    letter = r.json()
    assert letter["number"].startswith("CCT-") and letter["restoration_id"] == done["id"]  # fmt: skip
    assert letter["party_name"] == "Sri Veerabhadra Temple" and letter["amount"] is None  # fmt: skip
    text = pdf_text(client, h, letter, monkeypatch)
    for fragment in ("CERTIFICATE OF COMPLETION", "Sri Veerabhadra Temple", "recarved in teak", letter["number"]):  # fmt: skip
        assert fragment in text, fragment


def test_a_finishing_project_gets_no_certificate(client, people):
    h = people["owner"]["h"]
    piece = client.post("/items/", headers=h, json={"name": "Bought", "selling_price": 1, "item_type": "sculpture"}).json()  # fmt: skip
    p = client.post("/restorations", headers=h, json={"title": "Finish it", "kind": "finishing", "item_id": piece["id"]}).json()  # fmt: skip
    client.patch(f"/restorations/{p['id']}", headers=h, json={"status": "restored"})
    r = client.post("/letters/completion-certificate", headers=h, json={"restoration_id": p["id"], "work_done": "Painted"})  # fmt: skip
    assert r.status_code == 422


# ----------------------------------------------------------------- voiding
def test_a_voided_letter_stays_on_record_with_a_watermark(client, people, monkeypatch):
    owner = people["owner"]["h"]
    q = quotation(client, owner)
    assert client.post(f"/letters/{q['id']}/void", headers=owner, json={"reason": "x"}).status_code == 422  # fmt: skip
    r = client.post(f"/letters/{q['id']}/void", headers=owner, json={"reason": "Wrong customer"})  # fmt: skip
    assert r.status_code == 200
    v = r.json()
    assert v["status"] == "void" and v["void_reason"] == "Wrong customer" and v["voided_at"]  # fmt: skip
    assert client.post(f"/letters/{q['id']}/void", headers=owner, json={"reason": "again"}).status_code == 409  # fmt: skip
    text = pdf_text(client, owner, v, monkeypatch)
    assert "VOID" in text and "Wrong customer" in text
    listed = client.get("/letters", headers=owner, params={"status": "void", "search": q["number"]}).json()  # fmt: skip
    assert [x["id"] for x in listed] == [q["id"]]


def test_only_the_owner_can_void(client, people):
    q = quotation(client, people["owner"]["h"])
    for role in ("manager", "sales", "artisan", "worker"):
        r = client.post(f"/letters/{q['id']}/void", headers=people[role]["h"], json={"reason": "no way"})  # fmt: skip
        assert r.status_code == 403, role


# --------------------------------------------------------------- letterhead
def test_the_owner_sets_the_letterhead_and_old_letters_keep_theirs(
    client, people, monkeypatch
):
    owner, manager = people["owner"]["h"], people["manager"]["h"]
    before = quotation(client, owner)
    r = client.put(
        "/letters/profile",
        headers=owner,
        json={"name": "Vriksha Studios", "address": "12 Temple Road\nThorur", "phone": "9876543210", "gstin": "29ABCDE1234F1Z5", "signatory_name": "R. Nakama"},  # fmt: skip
    )
    assert r.status_code == 200 and r.json()["phone"] == "9876543210"
    assert client.get("/letters/profile", headers=manager).json()["gstin"] == "29ABCDE1234F1Z5"  # fmt: skip
    assert client.put("/letters/profile", headers=manager, json={"phone": "1"}).status_code == 403  # fmt: skip
    after = quotation(client, owner)
    text = pdf_text(client, owner, after, monkeypatch)
    assert "12 Temple Road" in text and "GSTIN: 29ABCDE1234F1Z5" in text
    old = pdf_text(client, owner, before, monkeypatch)
    assert "12 Temple Road" not in old
    # a field left out is kept; an empty one is cleared; the name cannot be emptied
    r = client.put("/letters/profile", headers=owner, json={"gstin": ""})
    assert r.json()["gstin"] is None and r.json()["phone"] == "9876543210"
    assert client.put("/letters/profile", headers=owner, json={"name": ""}).status_code == 422  # fmt: skip


# -------------------------------------------------------------- who sees what
def test_each_role_sees_only_the_letters_it_may_issue(client, people):
    owner = people["owner"]["h"]
    q = quotation(client, owner)
    supplier = new_supplier(client, owner)

    receipt = client.post("/letters/purchase-receipt", headers=owner, json={"supplier_id": supplier["id"], "description": "Piece", "amount": 100}).json()  # fmt: skip
    cert = client.post("/letters/completion-certificate", headers=owner, json={"restoration_id": restored_project(client, owner)["id"], "work_done": "Done"}).json()  # fmt: skip
    def seen(role):
        r = client.get("/letters", headers=people[role]["h"], params={"limit": 500})
        return {x["id"] for x in r.json()}

    assert {q["id"], receipt["id"], cert["id"]} <= seen("owner")
    assert {q["id"], receipt["id"]} <= seen("manager") and cert["id"] not in seen("manager")  # fmt: skip
    assert q["id"] in seen("sales") and not ({receipt["id"], cert["id"]} & seen("sales"))  # fmt: skip
    for role in (
        "sales",
        "manager",
    ):  # a letter they cannot see is not found, not forbidden
        h = people[role]["h"]
        assert client.get(f"/letters/{cert['id']}", headers=h).status_code == 404
        assert client.get(f"/letters/{cert['id']}/pdf", headers=h).status_code == 404
    assert client.get(f"/letters/{receipt['id']}", headers=people["sales"]["h"]).status_code == 404  # fmt: skip
    for role in ("artisan", "achari", "worker"):
        h = people[role]["h"]
        assert client.get("/letters", headers=h).status_code == 403
        assert client.get("/letters/profile", headers=h).status_code == 403
        assert client.get(f"/letters/{q['id']}/pdf", headers=h).status_code == 403


def test_issuing_is_limited_by_role(client, people):
    sales, manager = people["sales"]["h"], people["manager"]["h"]
    body = {"seller_name": "R", "description": "x", "amount": 5}
    assert client.post("/letters/purchase-receipt", headers=sales, json=body).status_code == 403  # fmt: skip
    cert = {"restoration_id": str(uuid.uuid4()), "work_done": "x"}
    assert client.post("/letters/completion-certificate", headers=manager, json=cert).status_code == 403  # fmt: skip
    assert client.post("/letters/quotation", headers=people["artisan"]["h"], json={"party_name": "x", "lines": LINES}).status_code == 403  # fmt: skip
    assert client.post("/letters/quotation", json={"party_name": "x", "lines": LINES}).status_code == 401  # fmt: skip


def test_the_list_filters_by_kind_and_search(client, people):
    h = people["owner"]["h"]
    name = f"Temple {uuid.uuid4().hex[:8]}"
    q = quotation(client, h, party_name=name)
    only = client.get("/letters", headers=h, params={"kind": "quotation", "search": name}).json()  # fmt: skip
    assert [x["id"] for x in only] == [q["id"]] and only[0]["kind_label"] == "Quotation"  # fmt: skip
    none = client.get("/letters", headers=h, params={"kind": "purchase_receipt", "search": name}).json()  # fmt: skip
    assert none == []
    assert Decimal(only[0]["amount"]) == Decimal("57501.00")
