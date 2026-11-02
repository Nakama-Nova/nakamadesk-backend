"""
Phone verification: a signed-in shopper proves a mobile number with a code texted by SMS.
Every text costs money, so the limits and the "Indian numbers only" rule are tested as
much as the happy path. No real SMS is sent: the sender is replaced, or boto3 is faked.
"""

import re
import uuid
from datetime import timedelta

import pytest

from app.core import ratelimit
from app.core.config import settings
from app.models.customer_account import CustomerAccount, PhoneCode
from app.services import customer_account_service as accounts
from app.services import phone_verification_service as service
from app.services import sms_service


@pytest.fixture(autouse=True)
def fresh_limits():
    ratelimit.reset()  # the per-IP limiter keeps counts for the whole test run
    yield
    ratelimit.reset()


@pytest.fixture
def texts(monkeypatch):
    """Every text the app tries to send, as (number, message), in order."""
    sent = []
    monkeypatch.setattr(
        sms_service, "send", lambda to, message: sent.append((to, message))
    )
    return sent


def new_phone():
    return f"9{uuid.uuid4().int % 10**9:09d}"  # a valid Indian mobile, unique per call


def new_account(db):
    account = CustomerAccount(
        email=f"shopper_{uuid.uuid4().hex[:10]}@example.com", name="Asha"
    )
    db.add(account)
    db.commit()
    db.refresh(account)
    return account


def bearer(account):
    return {"Authorization": f"Bearer {accounts.issue_token(account)}"}


def ask(client, account, phone):
    return client.post(
        "/account/phone/request-code", json={"phone": phone}, headers=bearer(account)
    )


def check(client, account, phone, code):
    return client.post(
        "/account/phone/verify",
        json={"phone": phone, "code": code},
        headers=bearer(account),
    )


def code_in(texts):
    return re.search(r"\b(\d{6})\b", texts[-1][1]).group(1)


def verified(client, db, texts):
    """A signed-in account with a verified phone. Returns (account, +91 number)."""
    account, phone = new_account(db), new_phone()
    assert ask(client, account, phone).status_code == 202
    assert check(client, account, phone, code_in(texts)).status_code == 200
    return account, f"+91{phone}"


# ------------------------------------------------------------------ asking for a code
def test_a_code_is_texted_to_the_number_in_international_form(client, db, texts):
    account, phone = new_account(db), new_phone()
    r = ask(client, account, f"{phone[:5]} {phone[5:]}")  # typed with a space
    assert r.status_code == 202
    assert len(texts) == 1
    assert texts[0][0] == f"+91{phone}"
    assert "Vriksha Studios" in texts[0][1] and re.search(r"\b\d{6}\b", texts[0][1])


def test_the_code_is_stored_only_as_a_hash(client, db, texts):
    account = new_account(db)
    ask(client, account, new_phone())
    row = db.query(PhoneCode).filter(PhoneCode.account_id == account.id).one()
    assert code_in(texts) not in row.code_hash
    assert len(row.code_hash) == 64


@pytest.mark.parametrize(
    "bad",
    [
        "+14155552671",  # not an allowed country
        "+44 7911 123456",
        "12345",  # not a number a phone has
        "+91 12345 67890",  # Indian mobiles start with 6 to 9
        "98765",
        "not a number",
    ],
)
def test_only_indian_mobile_numbers_get_a_code(client, db, texts, bad):
    r = ask(client, new_account(db), bad)
    assert r.status_code == 422
    assert texts == []


def test_you_must_be_signed_in_to_ask_for_or_check_a_code(client, texts):
    assert (
        client.post(
            "/account/phone/request-code", json={"phone": new_phone()}
        ).status_code
        == 401
    )
    assert (
        client.post(
            "/account/phone/verify", json={"phone": new_phone(), "code": "123456"}
        ).status_code
        == 401
    )
    assert texts == []


# ------------------------------------------------------------------ checking a code
def test_the_right_code_marks_the_phone_verified(client, db, texts):
    account, phone = new_account(db), new_phone()
    ask(client, account, phone)
    r = check(client, account, phone, code_in(texts))
    assert r.status_code == 200
    body = r.json()
    assert body["phone"] == f"+91{phone}"
    assert body["phone_verified_at"] is not None
    db.refresh(account)
    assert account.phone_verified_at is not None


def test_a_wrong_code_is_refused_and_five_wrong_tries_use_the_code_up(
    client, db, texts
):
    account, phone = new_account(db), new_phone()
    ask(client, account, phone)
    right = code_in(texts)
    wrong = "000000" if right != "000000" else "111111"
    for _ in range(service.MAX_ATTEMPTS):
        assert check(client, account, phone, wrong).status_code == 400
    assert check(client, account, phone, right).status_code == 400  # too late now


def test_a_code_works_once(client, db, texts):
    account, phone = new_account(db), new_phone()
    ask(client, account, phone)
    code = code_in(texts)
    assert check(client, account, phone, code).status_code == 200
    assert check(client, account, phone, code).status_code == 400


def test_an_expired_code_is_refused(client, db, texts):
    account, phone = new_account(db), new_phone()
    ask(client, account, phone)
    db.query(PhoneCode).filter(PhoneCode.account_id == account.id).update(
        {PhoneCode.expires_at: service._now() - timedelta(seconds=1)}
    )
    db.commit()
    assert check(client, account, phone, code_in(texts)).status_code == 400


def test_a_new_code_replaces_the_old_one(client, db, texts):
    account, phone = new_account(db), new_phone()
    ask(client, account, phone)
    old = code_in(texts)
    ask(client, account, phone)
    new = code_in(texts)
    if old != new:  # one time in a million they match
        assert check(client, account, phone, old).status_code == 400
    assert check(client, account, phone, new).status_code == 200


def test_a_code_for_one_number_does_not_verify_another(client, db, texts):
    account = new_account(db)
    ask(client, account, new_phone())
    assert check(client, account, new_phone(), code_in(texts)).status_code == 400


def test_someone_elses_code_does_not_work_for_me(client, db, texts):
    a, b, phone = new_account(db), new_account(db), new_phone()
    ask(client, a, phone)
    assert check(client, b, phone, code_in(texts)).status_code == 400


def test_a_number_verified_on_one_account_cannot_be_verified_on_another(
    client, db, texts
):
    _, phone = verified(client, db, texts)
    other = new_account(db)
    assert ask(client, other, phone).status_code == 202
    r = check(client, other, phone, code_in(texts))
    assert r.status_code == 409
    db.refresh(other)
    assert other.phone_verified_at is None


# ------------------------------------------------------------------ limits (they save money)
def test_an_account_gets_a_few_codes_an_hour(client, db, texts):
    account = new_account(db)
    for _ in range(service.MAX_CODES_PER_HOUR):
        assert ask(client, account, new_phone()).status_code == 202
    assert ask(client, account, new_phone()).status_code == 429
    assert len(texts) == service.MAX_CODES_PER_HOUR


def test_one_number_gets_a_few_codes_a_day_whoever_asks(client, db, texts):
    phone = new_phone()
    for _ in range(service.MAX_CODES_PER_PHONE_PER_DAY):
        assert ask(client, new_account(db), phone).status_code == 202
    assert ask(client, new_account(db), phone).status_code == 429
    assert len(texts) == service.MAX_CODES_PER_PHONE_PER_DAY


# ------------------------------------------------------------------ the profile
def test_changing_the_phone_in_the_profile_clears_the_verification(client, db, texts):
    account, phone = verified(client, db, texts)
    r = client.patch(
        "/account/me", json={"phone": new_phone()}, headers=bearer(account)
    )
    assert r.status_code == 200
    assert r.json()["phone_verified_at"] is None


def test_typing_the_verified_number_again_keeps_it_verified(client, db, texts):
    account, phone = verified(client, db, texts)
    typed = f"{phone[3:8]} {phone[8:]}"  # the same number as a shopper types it
    r = client.patch("/account/me", json={"phone": typed}, headers=bearer(account))
    assert r.status_code == 200
    body = r.json()
    assert body["phone"] == phone
    assert body["phone_verified_at"] is not None


def test_changing_only_the_name_keeps_the_phone_verified(client, db, texts):
    account, phone = verified(client, db, texts)
    r = client.patch("/account/me", json={"name": "Asha K"}, headers=bearer(account))
    assert r.json()["phone_verified_at"] is not None


# ------------------------------------------------------------------ when sending goes wrong
def test_production_without_sms_set_up_answers_503_and_sends_nothing(
    client, db, monkeypatch
):
    monkeypatch.setattr(settings, "IS_PROD", True)
    monkeypatch.setattr(settings, "SMS_PROVIDER", "")
    r = ask(client, new_account(db), new_phone())
    assert r.status_code == 503
    assert "not set up" in r.json()["detail"]


def test_a_failed_send_answers_503_without_leaking_the_error(client, db, monkeypatch):
    def boom(to, message):
        raise RuntimeError("AccessDenied: arn:aws:iam::1234:role/secret")

    monkeypatch.setattr(sms_service, "send", boom)
    r = ask(client, new_account(db), new_phone())
    assert r.status_code == 503
    assert "arn:" not in r.text


# ------------------------------------------------------------------ the SNS sender itself
class FakeSns:
    def __init__(self):
        self.calls = []

    def publish(self, **kwargs):
        self.calls.append(kwargs)


@pytest.fixture
def sns(monkeypatch):
    fake = FakeSns()
    monkeypatch.setattr(sms_service.boto3, "client", lambda *a, **k: fake)
    monkeypatch.setattr(settings, "SMS_PROVIDER", "sns")
    return fake


def test_sns_gets_a_transactional_message_with_the_india_dlt_ids(sns, monkeypatch):
    monkeypatch.setattr(settings, "SMS_SENDER_ID", "VRKSHA")
    monkeypatch.setattr(settings, "SMS_DLT_ENTITY_ID", "1101234567890")
    monkeypatch.setattr(settings, "SMS_DLT_TEMPLATE_ID", "1107000000000")
    sms_service.send("+919876543210", "Your code is 123456")
    (call,) = sns.calls
    assert call["PhoneNumber"] == "+919876543210"
    assert call["Message"] == "Your code is 123456"
    attrs = {k: v["StringValue"] for k, v in call["MessageAttributes"].items()}
    assert attrs == {
        "AWS.SNS.SMS.SMSType": "Transactional",
        "AWS.SNS.SMS.SenderID": "VRKSHA",
        "AWS.MM.SMS.EntityId": "1101234567890",
        "AWS.MM.SMS.TemplateId": "1107000000000",
    }


def test_sns_without_dlt_ids_sends_only_the_message_type(sns, monkeypatch):
    for name in ("SMS_SENDER_ID", "SMS_DLT_ENTITY_ID", "SMS_DLT_TEMPLATE_ID"):
        monkeypatch.setattr(settings, name, "")
    sms_service.send("+919876543210", "hi")
    attrs = sns.calls[0]["MessageAttributes"]
    assert list(attrs) == ["AWS.SNS.SMS.SMSType"]


def test_without_a_provider_development_only_logs_the_message(caplog, monkeypatch):
    monkeypatch.setattr(settings, "SMS_PROVIDER", "")
    monkeypatch.setattr(settings, "IS_PROD", False)
    with caplog.at_level("WARNING"):
        sms_service.send("+919876543210", "Your code is 123456")
    assert "123456" in caplog.text


# ------------------------------------------------------------------ number parsing
@pytest.mark.parametrize(
    "typed, expected",
    [
        ("98765 43210", "+919876543210"),
        ("9876543210", "+919876543210"),
        ("919876543210", "+919876543210"),
        ("+91 98765-43210", "+919876543210"),
        ("+91 (98765) 43210", "+919876543210"),
    ],
)
def test_numbers_are_put_in_international_form(typed, expected):
    assert sms_service.normalize_phone(typed) == expected


def test_other_countries_can_be_allowed_by_setting(monkeypatch):
    monkeypatch.setattr(settings, "SMS_ALLOWED_PREFIXES", ["+91", "+44"])
    assert sms_service.normalize_phone("+44 7911 123456") == "+447911123456"
