"""
Website customer accounts: sign in with an emailed one-time code, which also creates
the account the first time. Staff and customer logins must never work on each other's
routes.
"""

import re
import uuid
from datetime import timedelta

import pytest

from app.core import ratelimit
from app.models.customer_account import CustomerAccount, LoginCode
from app.services import customer_account_service as service
from app.services import email_service


@pytest.fixture(autouse=True)
def fresh_limits():
    ratelimit.reset()  # the per-address limiter keeps counts for the whole test run
    yield
    ratelimit.reset()


@pytest.fixture
def outbox(monkeypatch):
    """Every email the app tries to send, in order."""
    sent = []
    monkeypatch.setattr(
        email_service,
        "send",
        lambda to, subject, body: sent.append((to, subject, body)),
    )
    return sent


def new_email():
    return f"shopper_{uuid.uuid4().hex[:10]}@example.com"


def code_in(message):
    return re.search(r"\b(\d{6})\b", message[2]).group(1)


def ask(client, email):
    return client.post("/account/request-code", json={"email": email})


def sign_in(client, outbox, email, name="Asha", accept_terms=True, **extra):
    """Ask for a code, read it from the email, and verify it. Returns the response."""
    assert ask(client, email).status_code == 202
    return client.post(
        "/account/verify",
        json={
            "email": email,
            "code": code_in(outbox[-1]),
            "name": name,
            "accept_terms": accept_terms,
            **extra,
        },
    )


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


# ------------------------------------------------------------- asking for a code
def test_a_code_is_emailed_and_the_reply_is_the_same_for_every_address(
    client, outbox, db
):
    known = new_email()
    sign_in(client, outbox, known)
    sent_before = len(outbox)

    for email in (known, new_email()):
        r = ask(client, email)
        assert r.status_code == 202
        assert r.json() == {
            "message": "If that address can receive email, a code is on its way."
        }
    assert len(outbox) == sent_before + 2
    to, subject, body = outbox[-1]
    assert to != known and re.search(r"\b\d{6}\b", subject) and "10 minutes" in body


def test_the_code_is_stored_only_as_a_hash(client, outbox, db):
    email = new_email()
    ask(client, email)
    code = code_in(outbox[-1])
    row = db.query(LoginCode).filter(LoginCode.email == email).one()
    assert code not in row.code_hash and len(row.code_hash) == 64


@pytest.mark.parametrize("bad", ["", "nope", "a@", "@b.com", "a b@c.com"])
def test_a_bad_email_is_refused(client, outbox, bad):
    assert ask(client, bad).status_code == 422
    assert outbox == []


def test_email_case_and_spaces_do_not_make_a_second_account(client, outbox, db):
    base = new_email()
    first = sign_in(client, outbox, base)
    again = sign_in(client, outbox, f" {base.upper()} ", name=None, accept_terms=False)
    assert first.status_code == 200 and again.status_code == 200
    assert again.json()["is_new_account"] is False
    assert db.query(CustomerAccount).filter(CustomerAccount.email == base).count() == 1


def test_only_five_codes_an_hour_per_address(client, outbox):
    email = new_email()
    for _ in range(service.MAX_CODES_PER_HOUR + 3):
        assert ask(client, email).status_code == 202  # always the same reply
    assert len(outbox) == service.MAX_CODES_PER_HOUR


def test_one_address_cannot_use_up_anothers_allowance(client, outbox):
    spammed, other = new_email(), new_email()
    for _ in range(service.MAX_CODES_PER_HOUR + 1):
        ask(client, spammed)
    before = len(outbox)
    assert ask(client, other).status_code == 202 and len(outbox) == before + 1


def test_the_per_address_request_limit_answers_429(client, outbox):
    email = new_email()
    from app.core.config import settings

    codes = [
        ask(client, email).status_code for _ in range(settings.PUBLIC_RATE_LIMIT + 1)
    ]
    assert codes[-1] == 429 and set(codes[:-1]) == {202}


def test_an_unconfigured_production_server_says_so_instead_of_dropping_the_email(
    client, monkeypatch
):
    def boom(*a, **k):
        raise email_service.EmailNotConfigured("SMTP_HOST is not set")

    monkeypatch.setattr(email_service, "send", boom)
    r = ask(client, new_email())
    assert r.status_code == 503 and "not set up" in r.json()["detail"]


def test_a_mail_server_failure_does_not_reveal_anything(client, monkeypatch):
    def boom(*a, **k):
        raise OSError("connection refused")

    monkeypatch.setattr(email_service, "send", boom)
    assert ask(client, new_email()).status_code == 202


# ------------------------------------------------------------------ verifying
def test_first_sign_in_creates_the_account_and_returns_a_working_token(
    client, outbox, db
):
    email = new_email()
    r = sign_in(client, outbox, email, name="  Asha Rao  ")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["is_new_account"] is True and body["token_type"] == "bearer"
    assert body["expires_in"] == service.TOKEN_MINUTES * 60

    me = client.get("/account/me", headers=bearer(body["access_token"]))
    assert me.status_code == 200
    assert me.json()["email"] == email and me.json()["name"] == "Asha Rao"
    assert "token_version" not in me.json() and "password" not in str(me.json())

    row = db.query(CustomerAccount).filter(CustomerAccount.email == email).one()
    assert row.email_verified_at and row.terms_accepted_at and row.last_login_at


def test_a_returning_shopper_signs_in_with_just_the_code(client, outbox):
    email = new_email()
    sign_in(client, outbox, email)
    r = sign_in(client, outbox, email, name=None, accept_terms=False)
    assert r.status_code == 200 and r.json()["is_new_account"] is False


def test_a_new_account_needs_a_name_and_the_terms_but_the_code_survives(client, outbox):
    email = new_email()
    ask(client, email)
    code = code_in(outbox[-1])

    def verify(**extra):
        return client.post(
            "/account/verify", json={"email": email, "code": code, **extra}
        )

    assert verify().status_code == 422
    assert verify(name="Asha").status_code == 422  # terms not accepted
    assert verify(name="  ", accept_terms=True).status_code == 422
    ok = verify(name="Asha", accept_terms=True)  # the same code still works
    assert ok.status_code == 200


def test_a_wrong_code_is_refused_and_five_wrong_tries_burn_the_code(client, outbox):
    email = new_email()
    ask(client, email)
    real = code_in(outbox[-1])
    wrong = "000000" if real != "000000" else "111111"
    for _ in range(service.MAX_ATTEMPTS):
        r = client.post(
            "/account/verify",
            json={"email": email, "code": wrong, "name": "A", "accept_terms": True},
        )
        assert r.status_code == 400
    burned = client.post(
        "/account/verify",
        json={"email": email, "code": real, "name": "A", "accept_terms": True},
    )
    assert burned.status_code == 400  # even the right code is dead now
    assert sign_in(client, outbox, email).status_code == 200  # a new code works


def test_a_code_works_once(client, outbox):
    email = new_email()
    ask(client, email)
    body = {
        "email": email,
        "code": code_in(outbox[-1]),
        "name": "A",
        "accept_terms": True,
    }
    assert client.post("/account/verify", json=body).status_code == 200
    assert client.post("/account/verify", json=body).status_code == 400


def test_a_new_code_replaces_the_old_one(client, outbox):
    email = new_email()
    ask(client, email)
    old = code_in(outbox[-1])
    ask(client, email)
    new = code_in(outbox[-1])
    body = {"email": email, "name": "A", "accept_terms": True}
    if old != new:
        assert (
            client.post("/account/verify", json={**body, "code": old}).status_code
            == 400
        )
    assert client.post("/account/verify", json={**body, "code": new}).status_code == 200


def test_an_expired_code_is_refused(client, outbox, db):
    email = new_email()
    ask(client, email)
    row = db.query(LoginCode).filter(LoginCode.email == email).one()
    row.expires_at = row.expires_at - timedelta(minutes=service.CODE_MINUTES + 1)
    db.commit()
    r = client.post(
        "/account/verify",
        json={
            "email": email,
            "code": code_in(outbox[-1]),
            "name": "A",
            "accept_terms": True,
        },
    )
    assert r.status_code == 400


def test_a_code_for_one_address_does_not_open_another(client, outbox):
    mine, theirs = new_email(), new_email()
    ask(client, mine)
    code = code_in(outbox[-1])
    r = client.post(
        "/account/verify",
        json={"email": theirs, "code": code, "name": "A", "accept_terms": True},
    )
    assert r.status_code == 400


@pytest.mark.parametrize("code", ["12345", "1234567", "abcdef", "", "12 456"])
def test_a_malformed_code_is_refused(client, code):
    r = client.post("/account/verify", json={"email": new_email(), "code": code})
    assert r.status_code == 422


# ------------------------------------------------- staff and customers stay apart
def test_a_customer_token_opens_no_staff_route(client, outbox, people):
    token = sign_in(client, outbox, new_email()).json()["access_token"]
    for path in ("/auth/me", "/items/", "/auth/"):
        assert client.get(path, headers=bearer(token)).status_code == 401, path


def test_a_staff_token_is_not_a_customer_login(client, people):
    for role in ("owner", "manager", "sales", "worker"):
        r = client.get("/account/me", headers=people[role]["h"])
        assert r.status_code == 401, role


def test_no_token_or_a_junk_token_is_refused(client):
    assert client.get("/account/me").status_code == 401
    assert client.get("/account/me", headers=bearer("not-a-token")).status_code == 401


# --------------------------------------------------------------- your own account
def test_update_name_phone_and_marketing_consent(client, outbox):
    token = sign_in(client, outbox, new_email()).json()["access_token"]
    r = client.patch(
        "/account/me",
        headers=bearer(token),
        json={"name": "Asha R", "phone": "+91 98765 43210", "marketing_consent": True},
    )
    assert r.status_code == 200, r.text
    assert (r.json()["name"], r.json()["phone"], r.json()["marketing_consent"]) == (
        "Asha R",
        "+91 98765 43210",
        True,
    )
    again = client.patch("/account/me", headers=bearer(token), json={"phone": "98765"})
    assert again.status_code == 422  # too short to be a phone number
    still = client.get("/account/me", headers=bearer(token)).json()
    assert still["phone"] == "+91 98765 43210"


def test_the_email_and_status_cannot_be_changed_through_the_api(client, outbox):
    email = new_email()
    token = sign_in(client, outbox, email).json()["access_token"]
    client.patch(
        "/account/me",
        headers=bearer(token),
        json={"email": "evil@example.com", "is_active": False, "token_version": 99},
    )
    me = client.get("/account/me", headers=bearer(token))
    assert me.status_code == 200 and me.json()["email"] == email


def test_sign_out_everywhere_kills_every_token(client, outbox):
    email = new_email()
    first = sign_in(client, outbox, email).json()["access_token"]
    second = sign_in(client, outbox, email).json()["access_token"]
    assert client.post("/account/logout-all", headers=bearer(first)).status_code == 204
    for token in (first, second):
        assert client.get("/account/me", headers=bearer(token)).status_code == 401
    third = sign_in(client, outbox, email).json()["access_token"]
    assert client.get("/account/me", headers=bearer(third)).status_code == 200


def test_deleting_the_account_removes_the_data_and_frees_the_email(client, outbox, db):
    email = new_email()
    token = sign_in(client, outbox, email, name="Asha").json()["access_token"]
    client.patch(
        "/account/me",
        headers=bearer(token),
        json={"phone": "9876543210", "marketing_consent": True},
    )
    account_id = client.get("/account/me", headers=bearer(token)).json()["id"]

    assert client.delete("/account/me", headers=bearer(token)).status_code == 204
    assert client.get("/account/me", headers=bearer(token)).status_code == 401

    db.expire_all()
    row = db.get(CustomerAccount, uuid.UUID(account_id))
    assert row.is_active is False and row.name is None and row.phone is None
    assert email not in row.email and row.marketing_consent is False

    # the same address can start again, as a brand new account
    again = sign_in(client, outbox, email, name="Asha again")
    assert again.status_code == 200 and again.json()["is_new_account"] is True
    new_id = client.get(
        "/account/me", headers=bearer(again.json()["access_token"])
    ).json()["id"]
    assert new_id != account_id


def test_the_email_goes_out_from_notifications_with_replies_to_info(monkeypatch):
    from app.core.config import settings

    seen = {}

    class FakeSMTP:
        def __init__(self, host, port, timeout):
            seen["server"] = (host, port)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def starttls(self):
            seen["tls"] = True

        def login(self, user, password):
            seen["login"] = user

        def send_message(self, msg):
            seen["msg"] = msg

    monkeypatch.setattr(email_service.smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(settings, "SMTP_HOST", "smtp.example.com")
    monkeypatch.setattr(settings, "SMTP_USER", "mailer")
    monkeypatch.setattr(
        settings, "EMAIL_FROM", "Vriksha Studios <notifications@vrikshastudios.com>"
    )
    monkeypatch.setattr(settings, "EMAIL_REPLY_TO", "info@vrikshastudios.com")

    email_service.send("shopper@example.com", "Your code: 123456", "Body")
    msg = seen["msg"]
    assert seen["tls"] and seen["login"] == "mailer"
    assert msg["From"] == "Vriksha Studios <notifications@vrikshastudios.com>"
    assert msg["Reply-To"] == "info@vrikshastudios.com"
    assert msg["To"] == "shopper@example.com"
