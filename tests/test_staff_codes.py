"""Staff choose their own password from an emailed code: the owner never types one."""

import re
import uuid

import pytest

from app.core import ratelimit
from app.models.user import User
from app.services import email_service, staff_code_service


@pytest.fixture
def mailbox(monkeypatch):
    """Collect the emails the server would send."""
    ratelimit.reset()
    sent = []
    monkeypatch.setattr(
        email_service, "send", lambda to, subject, body: sent.append((to, subject, body))
    )
    return sent


def code_in(mail) -> str:
    return re.search(r"Your code is ([A-Z0-9]{8})", mail[2]).group(1)


def new_staff(auth_client, **extra):
    tag = uuid.uuid4().hex[:8]
    body = {
        "username": f"asha_{tag}",
        "email": f"asha_{tag}@example.com",
        "full_name": "Asha",
        "role": "sales",
        **extra,
    }
    return auth_client.post("/auth/register", json=body), body


def sign_in(client, username, password):
    return client.post("/auth/login", data={"username": username, "password": password})


def test_an_invited_person_sets_their_own_password(auth_client, mailbox):
    res, body = new_staff(auth_client)  # no password sent
    assert res.status_code == 200
    assert len(mailbox) == 1 and mailbox[0][0] == body["email"]
    assert body["username"] in mailbox[0][2]

    set_it = auth_client.post(
        "/auth/password/set",
        json={"email": body["email"], "code": code_in(mailbox[0]), "new_password": "my-own-pass1"},
    )
    assert set_it.status_code == 200
    assert sign_in(auth_client, body["username"], "my-own-pass1").status_code == 200


def test_nobody_can_sign_in_before_the_code_is_used(auth_client, mailbox):
    _, body = new_staff(auth_client)
    assert sign_in(auth_client, body["username"], "").status_code in (401, 422)
    assert sign_in(auth_client, body["username"], "password123").status_code == 401


def test_a_password_can_still_be_given_for_the_command_line(auth_client, mailbox):
    res, body = new_staff(auth_client, password="typed-by-script1")
    assert res.status_code == 200 and mailbox == []
    assert sign_in(auth_client, body["username"], "typed-by-script1").status_code == 200


def test_forgot_password_says_the_same_for_any_address(client, auth_client, mailbox):
    _, body = new_staff(auth_client)
    mailbox.clear()
    known = client.post("/auth/password/forgot", json={"email": body["email"]})
    unknown = client.post("/auth/password/forgot", json={"email": "nobody@example.com"})
    assert known.status_code == unknown.status_code == 200
    assert known.json() == unknown.json()
    assert len(mailbox) == 1  # only the real one got an email


def test_a_code_works_once_and_ends_the_old_sessions(client, auth_client, db, mailbox):
    _, body = new_staff(auth_client)
    first = code_in(mailbox[0])
    payload = {"email": body["email"], "code": first, "new_password": "first-pass-1"}
    assert client.post("/auth/password/set", json=payload).status_code == 200
    before = db.query(User).filter(User.email == body["email"]).first().token_version
    again = client.post("/auth/password/set", json={**payload, "new_password": "second-pass1"})
    assert again.status_code == 400  # used
    assert sign_in(client, body["username"], "second-pass1").status_code == 401

    mailbox.clear()
    client.post("/auth/password/forgot", json={"email": body["email"]})
    ok = {"email": body["email"], "code": code_in(mailbox[0]), "new_password": "third-pass-1"}
    assert client.post("/auth/password/set", json=ok).status_code == 200
    db.expire_all()
    assert db.query(User).filter(User.email == body["email"]).first().token_version > before


def test_a_wrong_code_is_refused_and_five_wrong_tries_lock_it(client, auth_client, mailbox):
    _, body = new_staff(auth_client)
    real = code_in(mailbox[0])
    wrong = "ZZZZZZZZ" if real != "ZZZZZZZZ" else "YYYYYYYY"
    for _ in range(staff_code_service.MAX_ATTEMPTS):
        r = client.post(
            "/auth/password/set",
            json={"email": body["email"], "code": wrong, "new_password": "whatever-123"},
        )
        assert r.status_code == 400
    locked = client.post(
        "/auth/password/set",
        json={"email": body["email"], "code": real, "new_password": "whatever-123"},
    )
    assert locked.status_code == 400  # even the right code, after too many wrong ones


def test_a_code_for_one_person_does_not_open_another(client, auth_client, mailbox):
    _, a = new_staff(auth_client)
    _, b = new_staff(auth_client)
    res = client.post(
        "/auth/password/set",
        json={"email": b["email"], "code": code_in(mailbox[0]), "new_password": "whatever-123"},
    )
    assert res.status_code == 400


def test_an_expired_code_is_refused(client, auth_client, db, mailbox):
    from app.models.staff_code import StaffCode

    _, body = new_staff(auth_client)
    db.query(StaffCode).update({StaffCode.expires_at: staff_code_service._now()})
    db.commit()
    res = client.post(
        "/auth/password/set",
        json={"email": body["email"], "code": code_in(mailbox[0]), "new_password": "whatever-123"},
    )
    assert res.status_code == 400


def test_at_most_three_codes_an_hour_per_person(auth_client, mailbox):
    res, body = new_staff(auth_client)  # the first code
    user_id = res.json()["id"]
    assert auth_client.post(f"/auth/users/{user_id}/send-invite").status_code == 200
    assert auth_client.post(f"/auth/users/{user_id}/send-invite").status_code == 200
    assert auth_client.post(f"/auth/users/{user_id}/send-invite").status_code == 429


def test_the_owner_still_gets_the_account_when_email_is_not_set_up(auth_client, db, monkeypatch):
    def refuse(*a, **k):
        raise email_service.EmailNotConfigured("no smtp")

    monkeypatch.setattr(email_service, "send", refuse)
    res, body = new_staff(auth_client)
    assert res.status_code == 503
    assert db.query(User).filter(User.email == body["email"]).first() is not None


def test_only_the_owner_can_send_an_invite(manager_client, db, mailbox):
    someone = User(
        username=f"w_{uuid.uuid4().hex[:8]}",
        email=f"w_{uuid.uuid4().hex[:8]}@example.com",
        password_hash="x",
        role="worker",
        is_active=True,
    )
    db.add(someone)
    db.commit()
    assert manager_client.post(f"/auth/users/{someone.id}/send-invite").status_code == 403
