"""Offer letters, payslips, experience letters and staff ID cards."""

import io
import uuid
from datetime import date
from decimal import Decimal

import pytest

from tests.test_hr import act_as, make, mark


@pytest.fixture
def owner(client, db):
    user = make(db, "owner")
    act_as(user)
    return user


def pdf(resp):
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"] == "application/pdf"
    assert resp.content.startswith(b"%PDF")
    return resp.content


def ready_staff(db, **kw):
    return make(
        db,
        full_name="Ravi Kumar",
        phone="9876543210",
        address="Channapatna",
        joining_date=date(2026, 2, 1),
        base_daily_wage=Decimal("800"),
        pay_amount=Decimal("800"),
        **kw,
    )


def test_offer_letter_uses_the_staff_record_and_prints(client, db, owner):
    staff = ready_staff(db)
    shift = client.post(
        "/shifts",
        json={
            "name": f"S{uuid.uuid4().hex[:5]}",
            "start_time": "09:00:00",
            "end_time": "17:00:00",
            "weekly_off": [6],
        },
    ).json()
    client.put(f"/people/users/{staff.id}/shift", json={"shift_id": shift["id"]})
    resp = client.post(
        "/letters/offer-letter",
        json={"user_id": str(staff.id), "designation": "Carver"},
    )
    assert resp.status_code == 201, resp.text
    letter = resp.json()
    assert letter["number"].startswith("OFL-") and letter["kind"] == "offer_letter"
    assert letter["party_name"] == "Ravi Kumar"
    assert Decimal(letter["amount"]) == Decimal("800")
    detail = client.get(f"/letters/{letter['id']}").json()["payload"]
    assert detail["pay_unit"] == "per day" and detail["employee"]["staff_code"]
    assert detail["shift"]["weekly_off"] == ["Sunday"]
    pdf(client.get(f"/letters/{letter['id']}/pdf"))


def test_offer_letter_needs_pay_and_a_joining_date(client, db, owner):
    no_pay = make(db, joining_date=date(2026, 2, 1))
    bad = client.post("/letters/offer-letter", json={"user_id": str(no_pay.id)})
    assert bad.status_code == 422
    no_date = make(db, base_daily_wage=Decimal("500"))
    bad = client.post("/letters/offer-letter", json={"user_id": str(no_date.id)})
    assert bad.status_code == 422
    ok = client.post(
        "/letters/offer-letter",
        json={
            "user_id": str(no_date.id),
            "joining_date": "2026-03-01",
            "pay_type": "monthly",
            "pay_amount": "20000",
        },
    )
    assert ok.status_code == 201
    missing = client.post("/letters/offer-letter", json={"user_id": str(uuid.uuid4())})
    assert missing.status_code == 404


def test_offer_letters_are_owner_only_payslips_are_not(client, db, owner):
    staff = ready_staff(db)
    act_as(make(db, "manager"))
    offer = client.post("/letters/offer-letter", json={"user_id": str(staff.id)})
    assert offer.status_code == 403
    exp = client.post("/letters/experience-letter", json={"user_id": str(staff.id)})
    assert exp.status_code == 403
    act_as(owner)
    client.post("/letters/offer-letter", json={"user_id": str(staff.id)})
    act_as(make(db, "manager"))
    assert "offer_letter" not in {x["kind"] for x in client.get("/letters").json()}
    act_as(make(db, "worker"))
    assert client.get("/letters").status_code == 403


def payout(client, staff):
    mark(client, staff, date(2026, 3, 2), "present", "800")
    mark(client, staff, date(2026, 3, 3), "half-day", "800")
    client.post(
        "/payroll/work-entries",
        json={
            "user_id": str(staff.id),
            "work_date": "2026-03-02",
            "kind": "bonus",
            "description": "Diwali",
            "rate": "300",
        },
    )
    client.post(
        "/payroll/advances",
        json={"user_id": str(staff.id), "given_on": "2026-03-01", "amount": "200"},
    )
    resp = client.post(
        "/payroll/pay",
        json={
            "user_id": str(staff.id),
            "through": "2026-03-10",
            "deduction": "200",
            "method": "upi",
            "reference": "UTR123",
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_payslip_shows_the_pay_out_and_is_issued_once(client, db, owner):
    staff = ready_staff(db)
    paid = payout(client, staff)  # 1200 wages + 300 bonus - 200 taken back = 1300
    first = client.post("/letters/payslip", json={"payment_id": paid["id"]})
    assert first.status_code == 201, first.text
    slip = first.json()
    assert slip["number"].startswith("PSL-")
    assert Decimal(slip["amount"]) == Decimal(paid["net_paid"]) == Decimal("1300")
    body = client.get(f"/letters/{slip['id']}").json()["payload"]
    assert body["gross"] == "1500.00" and body["net"] == "1300.00"
    assert body["deductions"][0]["amount"] == "200.00"
    assert {e["label"] for e in body["earnings"]} == {"Wages", "Diwali"}
    assert body["attendance"]["present"] == 1 and body["attendance"]["half-day"] == 1
    pdf(client.get(f"/letters/{slip['id']}/pdf"))
    again = client.post("/letters/payslip", json={"payment_id": paid["id"]})
    assert again.json()["id"] == slip["id"]
    nope = client.post("/letters/payslip", json={"payment_id": str(uuid.uuid4())})
    assert nope.status_code == 404


def test_a_person_opens_their_own_payslip_but_not_anyone_elses(client, db, owner):
    staff = ready_staff(db)
    other = ready_staff(db)
    paid = payout(client, staff)
    act_as(staff)
    pdf(client.get(f"/payroll/payments/{paid['id']}/payslip.pdf"))
    act_as(other)
    assert client.get(f"/payroll/payments/{paid['id']}/payslip.pdf").status_code == 404
    act_as(make(db, "manager"))
    pdf(client.get(f"/payroll/payments/{paid['id']}/payslip.pdf"))


def test_experience_letter_needs_the_person_to_have_left(client, db, owner):
    staff = ready_staff(db)
    early = client.post("/letters/experience-letter", json={"user_id": str(staff.id)})
    assert early.status_code == 409
    client.post(
        f"/people/users/{staff.id}/offboard", json={"last_working_date": "2026-06-30"}
    )
    resp = client.post(
        "/letters/experience-letter",
        json={
            "user_id": str(staff.id),
            "designation": "Carver",
            "remarks": "Skilled and punctual.",
        },
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["number"].startswith("EXL-")
    pdf(client.get(f"/letters/{resp.json()['id']}/pdf"))
    void = client.post(
        f"/letters/{resp.json()['id']}/void", json={"reason": "wrong name"}
    )
    assert void.status_code == 200


def test_hr_letters_can_be_edited_and_keep_their_number(client, db, owner):
    staff = ready_staff(db)
    letter = client.post(
        "/letters/offer-letter", json={"user_id": str(staff.id), "probation_months": 3}
    ).json()
    edited = client.put(
        f"/letters/{letter['id']}",
        json={"user_id": str(staff.id), "probation_months": 6},
    )
    assert edited.status_code == 200 and edited.json()["number"] == letter["number"]
    shown = client.get(f"/letters/{letter['id']}").json()["payload"]
    assert shown["probation_months"] == 6


def test_staff_code_is_given_once_and_never_changes(client, db, owner):
    staff = make(db)
    pdf(client.get(f"/people/users/{staff.id}/id-card.pdf"))
    db.refresh(staff)
    code = staff.staff_code
    assert code and "-" in code
    pdf(client.get(f"/people/users/{staff.id}/id-card.pdf"))
    db.refresh(staff)
    assert staff.staff_code == code
    other = make(db)
    pdf(client.get(f"/people/users/{other.id}/id-card.pdf"))
    db.refresh(other)
    assert other.staff_code != code
    tag = uuid.uuid4().hex[:6]
    made = client.post(
        "/auth/register",
        json={
            "username": f"n_{tag}",
            "email": f"n_{tag}@example.com",
            "role": "worker",
            "password": "longenough1",
        },
    )
    assert made.json()["staff_code"]


def test_id_card_access_and_photo(client, db, owner, store):
    from PIL import Image

    staff = ready_staff(db)
    buf = io.BytesIO()
    Image.new("RGB", (64, 64), "#8a5a2b").save(buf, "PNG")
    png = buf.getvalue()
    ticket = client.post(
        f"/people/users/{staff.id}/documents/upload-url",
        json={
            "kind": "photo",
            "filename": "me.png",
            "content_type": "image/png",
            "size": len(png),
        },
    ).json()
    store.objects[store.signed[-1][0]] = png
    client.post(f"/people/users/{staff.id}/documents/{ticket['document_id']}/confirm")
    with_photo = pdf(client.get(f"/people/users/{staff.id}/id-card.pdf"))
    plain = pdf(client.get(f"/people/users/{ready_staff(db).id}/id-card.pdf"))
    assert len(with_photo) != len(plain)
    act_as(staff)
    pdf(client.get(f"/people/users/{staff.id}/id-card.pdf"))  # your own
    act_as(make(db, "worker"))
    assert client.get(f"/people/users/{staff.id}/id-card.pdf").status_code == 403
    act_as(make(db, "manager"))
    pdf(client.get(f"/people/users/{staff.id}/id-card.pdf"))
    act_as(owner)
    assert client.get(f"/people/users/{uuid.uuid4()}/id-card.pdf").status_code == 404
