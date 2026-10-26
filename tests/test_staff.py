"""Staff accounts: edit, switch off, reset password, wages, and who may do what."""

import uuid

from app.core.security import create_access_token


def new_staff(client, owner, role="achari", **extra):
    name = f"{role}_{uuid.uuid4().hex[:6]}"
    body = {
        "username": name,
        "email": f"{name}@example.com",
        "password": "StartPass123",
        "role": role,
        "full_name": "Asha K",
        "base_daily_wage": 800,
        **extra,
    }
    r = client.post("/auth/register", headers=owner, json=body)
    assert r.status_code == 200, r.text
    return r.json(), name


def login(client, name, password):
    return client.post("/auth/login", data={"username": name, "password": password})


def test_owner_edits_a_staff_account_including_the_daily_wage(client, people):
    owner = people["owner"]["h"]
    user, _ = new_staff(client, owner)
    assert float(user["base_daily_wage"]) == 800
    r = client.patch(
        f"/auth/users/{user['id']}",
        headers=owner,
        json={
            "full_name": "Asha Kumari",
            "phone": "98765 43210",
            "role": "manager",
            "base_daily_wage": 950,
        },
    )
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["full_name"] == "Asha Kumari" and out["role"] == "manager"
    assert float(out["base_daily_wage"]) == 950
    assert out["username"] == user["username"]  # untouched fields stay


def test_only_the_owner_manages_staff(client, people):
    owner = people["owner"]["h"]
    user, _ = new_staff(client, owner)
    for role in ("manager", "sales", "achari", "worker"):
        h = people[role]["h"]
        edit = client.patch(
            f"/auth/users/{user['id']}", headers=h, json={"role": "owner"}
        )
        assert edit.status_code == 403
        reset = client.post(
            f"/auth/users/{user['id']}/reset-password",
            headers=h,
            json={"new_password": "NewPass1234"},
        )
        assert reset.status_code == 403


def test_switching_an_account_off_stops_login_and_existing_tokens(client, people):
    owner = people["owner"]["h"]
    user, name = new_staff(client, owner)
    token = login(client, name, "StartPass123").json()["access_token"]
    me = {"Authorization": f"Bearer {token}"}
    assert client.get("/auth/me", headers=me).status_code == 200

    off = client.patch(
        f"/auth/users/{user['id']}", headers=owner, json={"is_active": False}
    )
    assert off.status_code == 200 and off.json()["is_active"] is False
    assert off.json()["status"] == "inactive"
    assert login(client, name, "StartPass123").status_code == 401
    assert client.get("/auth/me", headers=me).status_code == 401  # old token too

    on = client.patch(
        f"/auth/users/{user['id']}", headers=owner, json={"is_active": True}
    )
    assert on.json()["status"] == "active"
    assert login(client, name, "StartPass123").status_code == 200


def test_you_cannot_lock_yourself_out_and_the_last_owner_stays(client, people):
    owner = people["owner"]
    own = f"/auth/users/{owner['id']}"
    h = owner["h"]
    assert client.patch(own, headers=h, json={"is_active": False}).status_code == 400
    assert client.patch(own, headers=h, json={"role": "manager"}).status_code == 400
    assert client.patch(own, headers=h, json={"full_name": "Me"}).status_code == 200

    # a second owner can demote the first, but then they are the last owner
    second, _ = new_staff(client, h, role="owner")
    token = create_access_token({"sub": second["id"]})
    second_h = {"Authorization": f"Bearer {token}"}
    assert (
        client.patch(own, headers=second_h, json={"role": "manager"}).status_code == 200
    )
    # an owner who is the only active owner cannot be switched off by anyone else
    third, _ = new_staff(client, second_h, role="manager")
    third_h = {"Authorization": f"Bearer {create_access_token({'sub': third['id']})}"}
    assert (
        client.patch(
            f"/auth/users/{second['id']}", headers=third_h, json={"is_active": False}
        ).status_code
        == 403
    )  # a manager cannot manage staff at all


def test_reset_and_change_password(client, people):
    owner = people["owner"]["h"]
    user, name = new_staff(client, owner)
    reset = f"/auth/users/{user['id']}/reset-password"
    assert (
        client.post(reset, headers=owner, json={"new_password": "short"}).status_code
        == 422
    )
    missing = f"/auth/users/{uuid.uuid4()}/reset-password"
    assert (
        client.post(
            missing, headers=owner, json={"new_password": "NewPass1234"}
        ).status_code
        == 404
    )
    assert (
        client.post(
            reset, headers=owner, json={"new_password": "ResetPass123"}
        ).status_code
        == 200
    )
    assert login(client, name, "StartPass123").status_code == 401
    token = login(client, name, "ResetPass123").json()["access_token"]
    me = {"Authorization": f"Bearer {token}"}

    wrong = {"current_password": "wrong", "new_password": "MyOwnPass123"}
    assert client.post("/auth/me/password", headers=me, json=wrong).status_code == 400
    right = {"current_password": "ResetPass123", "new_password": "MyOwnPass123"}
    assert client.post("/auth/me/password", headers=me, json=right).status_code == 200
    assert login(client, name, "MyOwnPass123").status_code == 200


def test_duplicate_username_and_email_are_refused_clearly(client, people):
    owner = people["owner"]["h"]
    user, name = new_staff(client, owner)
    again = client.post(
        "/auth/register",
        headers=owner,
        json={
            "username": name,
            "email": "other@example.com",
            "password": "StartPass123",
            "role": "worker",
        },
    )
    assert again.status_code == 400 and "Username" in again.json()["detail"]
    same_mail = client.post(
        "/auth/register",
        headers=owner,
        json={
            "username": f"x_{uuid.uuid4().hex[:6]}",
            "email": user["email"],
            "password": "StartPass123",
            "role": "worker",
        },
    )
    assert same_mail.status_code == 400 and "Email" in same_mail.json()["detail"]
    other, _ = new_staff(client, owner)
    r = client.patch(
        f"/auth/users/{other['id']}", headers=owner, json={"email": user["email"]}
    )
    assert r.status_code == 400 and "Email" in r.json()["detail"]


def test_short_passwords_are_refused_at_registration(client, people):
    r = client.post(
        "/auth/register",
        headers=people["owner"]["h"],
        json={
            "username": f"s_{uuid.uuid4().hex[:6]}",
            "email": f"{uuid.uuid4().hex[:6]}@example.com",
            "password": "short",
            "role": "worker",
        },
    )
    assert r.status_code == 422
