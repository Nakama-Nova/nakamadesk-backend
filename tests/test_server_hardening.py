"""Hardening: bill discounts, sign-in guessing, deleting a product that has history."""

import uuid

from app.models.login_attempt import LoginAttempt
from app.services import login_guard


def make_item(client, h, **kw):
    r = client.post(
        "/items/",
        headers=h,
        json={
            "sku": f"H-{uuid.uuid4().hex[:6]}",
            "name": "Chair",
            "selling_price": 1000,
            "current_stock": 10,
            **kw,
        },
    )
    assert r.status_code == 200, r.text
    return r.json()


def bill(client, h, item_id, **kw):
    return client.post(
        "/sales/",
        headers=h,
        json={
            "items": [{"item_id": item_id, "quantity": 1}],
            "payment_method": "cash",
            **kw,
        },
    )


# ------------------------------------------------------------------ discounts
def test_a_discount_cannot_be_negative(client, people):
    it = make_item(client, people["owner"]["h"])
    r = bill(client, people["sales"]["h"], it["id"], discount=-500)
    assert r.status_code == 422  # it used to raise the bill to 1500


def test_a_discount_cannot_be_more_than_the_bill(client, people):
    it = make_item(client, people["owner"]["h"])
    r = bill(client, people["sales"]["h"], it["id"], discount=5000)
    assert r.status_code == 400 and "more than the bill" in r.json()["detail"]
    # and nothing was sold: the stock is untouched
    after = client.get(f"/items/{it['id']}", headers=people["owner"]["h"]).json()
    assert after["current_stock"] == 10


def test_a_normal_discount_still_works_up_to_the_full_bill(client, people):
    owner, sales = people["owner"]["h"], people["sales"]["h"]
    it = make_item(client, owner)
    ok = bill(client, sales, it["id"], discount=250)
    assert ok.status_code == 200 and float(ok.json()["total_amount"]) == 750
    full = bill(client, sales, it["id"], discount=1000)  # a free item is allowed
    assert full.status_code == 200 and float(full.json()["total_amount"]) == 0


# ------------------------------------------------------------------ sign-in guessing
def wrong(client, username, ip="10.0.0.1"):
    return client.post(
        "/auth/login",
        data={"username": username, "password": "wrong-guess"},
        headers={"X-Forwarded-For": ip},
    )


def test_repeated_wrong_passwords_lock_that_user_from_that_address(client, people, db):
    user = f"owner_{uuid.uuid4().hex[:6]}"
    for _ in range(login_guard.MAX_PER_USER_IP):
        assert wrong(client, user, "203.0.113.7").status_code == 401
    r = wrong(client, user, "203.0.113.7")
    assert r.status_code == 429 and "Too many" in r.json()["detail"]
    assert r.headers["retry-after"] == str(login_guard.WINDOW_MINUTES * 60)


def test_while_locked_even_the_right_password_is_refused(client, people, db):
    owner = people["owner"]
    me = client.get("/auth/me", headers=owner["h"]).json()
    ip = "203.0.113.9"
    for _ in range(login_guard.MAX_PER_USER_IP):
        wrong(client, me["username"], ip)
    # the real password still cannot be confirmed from that address
    from app.models.user import User

    db.query(User).filter(User.id == me["id"]).update(
        {
            "password_hash": __import__(
                "app.services.auth_service", fromlist=["x"]
            ).hash_password("RightPass123")
        }
    )
    db.commit()
    locked = client.post(
        "/auth/login",
        data={"username": me["username"], "password": "RightPass123"},
        headers={"X-Forwarded-For": ip},
    )
    assert locked.status_code == 429
    # from another address it works
    other = client.post(
        "/auth/login",
        data={"username": me["username"], "password": "RightPass123"},
        headers={"X-Forwarded-For": "198.51.100.2"},
    )
    assert other.status_code == 200


def test_a_successful_sign_in_clears_the_failures(client, people, db):
    me = client.get("/auth/me", headers=people["owner"]["h"]).json()
    from app.models.user import User
    from app.services import auth_service

    db.query(User).filter(User.id == me["id"]).update(
        {"password_hash": auth_service.hash_password("RightPass123")}
    )
    db.commit()
    ip = "203.0.113.20"
    for _ in range(login_guard.MAX_PER_USER_IP - 1):
        wrong(client, me["username"], ip)
    ok = client.post(
        "/auth/login",
        data={"username": me["username"], "password": "RightPass123"},
        headers={"X-Forwarded-For": ip},
    )
    assert ok.status_code == 200
    assert (
        db.query(LoginAttempt)
        .filter(LoginAttempt.username == me["username"].lower(), LoginAttempt.ip == ip)
        .count()
        == 0
    )


def test_one_address_trying_many_users_is_stopped(client, people, db):
    ip = "203.0.113.30"
    codes = [
        wrong(client, f"nobody_{i}", ip).status_code
        for i in range(login_guard.MAX_PER_IP + 3)
    ]
    assert codes[: login_guard.MAX_PER_IP] == [401] * login_guard.MAX_PER_IP
    assert set(codes[login_guard.MAX_PER_IP :]) == {429}
    # another address is not affected
    assert wrong(client, "nobody_x", "203.0.113.31").status_code == 401


def test_one_user_guessed_from_many_addresses_is_stopped(client, people, db):
    user = f"spread_{uuid.uuid4().hex[:6]}"
    codes = [
        wrong(client, user, f"192.0.2.{i}").status_code
        for i in range(login_guard.MAX_PER_USER + 2)
    ]
    assert 429 in codes[login_guard.MAX_PER_USER :]


def test_old_failures_are_forgotten(client, people, db):
    from datetime import datetime, timedelta, timezone

    user = f"old_{uuid.uuid4().hex[:6]}"
    old = datetime.now(timezone.utc) - timedelta(minutes=login_guard.WINDOW_MINUTES + 1)
    for _ in range(login_guard.MAX_PER_USER_IP + 2):
        db.add(LoginAttempt(username=user, ip="203.0.113.40", at=old))
    db.commit()
    assert wrong(client, user, "203.0.113.40").status_code == 401  # not locked


def test_unknown_user_and_wrong_password_look_the_same(client, people):
    me = client.get("/auth/me", headers=people["owner"]["h"]).json()
    a = wrong(client, me["username"], "203.0.113.50")
    b = wrong(client, f"ghost_{uuid.uuid4().hex[:6]}", "203.0.113.51")
    assert a.status_code == b.status_code == 401 and a.json() == b.json()


def test_a_switched_off_account_is_only_revealed_to_someone_with_the_password(
    client, people, db
):

    owner = people["owner"]["h"]
    name = f"off_{uuid.uuid4().hex[:6]}"
    u = client.post(
        "/auth/register",
        headers=owner,
        json={
            "username": name,
            "email": f"{name}@example.com",
            "password": "StartPass123",
            "role": "worker",
        },
    ).json()
    client.patch(f"/auth/users/{u['id']}", headers=owner, json={"is_active": False})
    guess = wrong(client, name, "203.0.113.60")
    assert guess.json()["detail"] == "Invalid credentials"  # a guesser learns nothing
    known = client.post(
        "/auth/login",
        data={"username": name, "password": "StartPass123"},
        headers={"X-Forwarded-For": "203.0.113.61"},
    )
    assert known.status_code == 401 and "switched off" in known.json()["detail"]


# ------------------------------------------------------------------ deleting a product with history
def test_deleting_a_product_that_was_sold_is_a_clear_409_not_a_crash(client, people):
    owner = people["owner"]["h"]
    it = make_item(client, owner)
    assert bill(client, owner, it["id"]).status_code == 200
    r = client.delete(f"/items/{it['id']}", headers=owner)
    assert r.status_code == 409
    assert (
        "sales or stock history" in r.json()["detail"]
        and "Switch it off" in r.json()["detail"]
    )
    # still there, and switching it off works
    assert client.get(f"/items/{it['id']}", headers=owner).status_code == 200
    off = client.put(f"/items/{it['id']}", headers=owner, json={"is_active": False})
    assert off.status_code == 200 and off.json()["is_active"] is False


def test_a_product_with_no_history_still_deletes(client, people):
    owner = people["owner"]["h"]
    it = make_item(client, owner, current_stock=0)
    assert client.delete(f"/items/{it['id']}", headers=owner).status_code == 200
    assert client.get(f"/items/{it['id']}", headers=owner).status_code == 404
