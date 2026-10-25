from fastapi.testclient import TestClient


def test_invoice_generation_flow(auth_client: TestClient):
    # 1. Create an Item
    item_payload = {
        "sku": "INV-ITEM-001",
        "name": "Dining Table 1",
        "category_id": None,
        "selling_price": 10000,
        "current_stock": 10,
        "hsn_code": "9403",
        "gst_percent": 18.0,
    }
    response = auth_client.post("/items/", json=item_payload)
    assert response.status_code == 200
    item_id = response.json()["id"]

    # 2. Create a Customer
    customer_payload = {
        "name": "Ramesh",
        "email": "ramesh@example.com",
        "phone": "9876543210",
        "address": "Bangalore",
    }
    response = auth_client.post("/customers/", json=customer_payload)
    assert response.status_code == 200
    customer_id = response.json()["id"]

    # 3. Create a Sale
    sale_payload = {
        "customer_id": customer_id,
        "items": [{"item_id": item_id, "quantity": 1}],
    }
    response = auth_client.post("/sales/", json=sale_payload)
    assert response.status_code == 200
    sale_data = response.json()

    # 4. Verify invoice_number exists on sale response
    invoice_number = sale_data.get("invoice_number")
    assert invoice_number is not None
    assert invoice_number.startswith("NTD-")

    # 5. Verify GET /invoices returns list
    response = auth_client.get("/invoices/")
    assert response.status_code == 200
    invoices_list = response.json()
    assert len(invoices_list) > 0
    assert any(inv["invoice_number"] == invoice_number for inv in invoices_list)

    # 6. Verify GET /invoices/{invoice_number}
    response = auth_client.get(f"/invoices/{invoice_number}")
    assert response.status_code == 200
    invoice_data = response.json()
    assert invoice_data["invoice_number"] == invoice_number
    assert invoice_data["customer"]["name"] == "Ramesh"

    # 7. Verify GET /invoices/{invoice_number}/pdf
    response = auth_client.get(f"/invoices/{invoice_number}/pdf")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"


def _bill_no(invoice_number: str) -> int:
    return int(invoice_number.rsplit("-", 1)[1])


def test_invoice_numbers_are_consecutive_without_gaps(auth_client: TestClient):
    item = auth_client.post(
        "/items/",
        json={
            "sku": "INV-SEQ-001",
            "name": "Stool",
            "selling_price": 500,
            "current_stock": 3,
        },
    ).json()

    def sell(qty: int):
        return auth_client.post(
            "/sales/", json={"items": [{"item_id": item["id"], "quantity": qty}]}
        )

    first = sell(1)
    assert first.status_code == 200
    # Fails on stock (3 - 1 = 2 left): the rollback must hand its number back.
    assert sell(99).status_code == 400
    second = sell(1)
    third = sell(1)

    numbers = [_bill_no(r.json()["invoice_number"]) for r in (first, second, third)]
    assert numbers[1] == numbers[0] + 1
    assert numbers[2] == numbers[1] + 1
    assert len(first.json()["invoice_number"]) <= 16


def _pdf_text(auth_client: TestClient, invoice_number: str) -> str:
    """Readable page text of the invoice PDF (reportlab: ASCII85, maybe Flate)."""
    import base64
    import re
    import zlib

    raw = auth_client.get(f"/invoices/{invoice_number}/pdf").content
    out = b""
    pattern = rb"stream\r?\n(.*?)\s*endstream"
    for m in re.finditer(pattern, raw, re.S):
        data = m.group(1).strip()
        if data.endswith(b"~>"):
            data = base64.a85decode(data[:-2].removeprefix(b"<~"))
        try:
            data = zlib.decompress(data)
        except zlib.error:
            pass
        out += data
    return out.decode("latin-1")


def test_zero_tax_sale_prints_plain_bill(auth_client: TestClient):
    item = auth_client.post(
        "/items/",
        json={
            "sku": "BILL-001",
            "name": "Plain Stool",
            "selling_price": 500,
            "current_stock": 5,
        },
    ).json()
    sale = auth_client.post(
        "/sales/", json={"items": [{"item_id": item["id"], "quantity": 1}]}
    ).json()
    text = _pdf_text(auth_client, sale["invoice_number"])
    assert "Bill:" in text
    assert "Vriksha Furnitures" in text
    assert "Nakama" not in text
    assert "CGST" not in text


def test_taxed_sale_still_prints_tax_invoice(auth_client: TestClient):
    item = auth_client.post(
        "/items/",
        json={
            "sku": "BILL-002",
            "name": "Taxed Stool",
            "selling_price": 500,
            "current_stock": 5,
            "gst_percent": 18.0,
        },
    ).json()
    sale = auth_client.post(
        "/sales/", json={"items": [{"item_id": item["id"], "quantity": 1}]}
    ).json()
    text = _pdf_text(auth_client, sale["invoice_number"])
    assert "Invoice:" in text
    assert "CGST" in text
