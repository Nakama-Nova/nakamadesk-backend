"""A Lab link opens the viewer and NOTHING else: it must never work as a login."""

import uuid

import pytest
from jose import jwt

from app.core.security import (
    ALGORITHM,
    SECRET_KEY,
    create_access_token,
    create_scoped_token,
)
from app.models.user import User
from tests.lab_fixtures import chair_parts


def link_token(client, people, chair, role="achari", mode="view"):
    r = client.post(
        "/lab/links",
        headers=people[role]["h"],
        json={"item_id": chair["id"], "mode": mode},
    )
    assert r.status_code == 200, r.text
    return r.json()


def open_session(client, people, chair, role="achari", mode="view"):
    """Follow a Lab link: returns the redirect response; the cookie lands in `client`."""
    out = link_token(client, people, chair, role, mode)
    return client.get(out["path"], follow_redirects=False)


def test_link_opens_the_viewer_and_sets_an_http_only_lab_cookie(client, people, chair):
    out = link_token(client, people, chair)
    assert out["url"].startswith("http") and "/lab/open?t=" in out["url"]
    r = client.get(out["path"], follow_redirects=False)
    assert r.status_code == 303
    assert (
        r.headers["location"] == f"/lab/view/{chair['id']}"
    )  # token is gone from the URL
    assert r.headers["referrer-policy"] == "no-referrer"
    cookie = r.headers["set-cookie"].lower()
    assert (
        "lab_session=" in cookie and "httponly" in cookie and "samesite=lax" in cookie
    )
    assert "path=/lab" in cookie

    page = client.get(f"/lab/view/{chair['id']}")
    assert page.status_code == 200 and page.headers["cache-control"] == "no-store"
    assert page.headers["referrer-policy"] == "no-referrer"
    assert "frame-ancestors 'none'" in page.headers["content-security-policy"]


def test_the_cookie_can_read_the_lab_but_nothing_else(client, people, chair):
    open_session(client, people, chair)
    assert client.get(f"/lab/items/{chair['id']}").status_code == 200
    assert client.get("/lab/items").status_code == 200
    # the cookie is scoped to /lab and its token is rejected as a login anyway
    for path in (
        "/items/",
        "/auth/me",
        "/orders/",
        "/sales/",
        "/customers/",
        "/dashboard/summary",
    ):
        assert client.get(path).status_code == 401, path


def test_a_scoped_token_is_rejected_as_a_bearer_login_everywhere(client, people, chair):
    uid = people["owner"]["id"]
    for scope in ("lab-link", "lab", "anything"):
        h = {"Authorization": f"Bearer {create_scoped_token(uid, scope, 10)}"}
        for path in ("/auth/me", "/items/", "/orders/", "/sales/", "/lab/items"):
            assert client.get(h and path, headers=h).status_code == 401, (scope, path)
    # and a normal login token still works
    assert client.get("/auth/me", headers=people["owner"]["h"]).status_code == 200


def test_the_link_token_cannot_be_used_as_the_session_cookie(client, people, chair):
    out = link_token(client, people, chair)
    token = out["path"].split("t=", 1)[1]
    client.cookies.set("lab_session", token, path="/lab")
    assert (
        client.get(f"/lab/items/{chair['id']}").status_code == 401
    )  # scope is lab-link, not lab


def test_a_login_token_is_not_a_valid_link(client, people, chair):
    r = client.get(
        "/lab/open",
        params={"t": people["owner"]["h"]["Authorization"][7:]},
        follow_redirects=False,
    )
    assert r.status_code == 401
    assert (
        client.get(
            "/lab/open", params={"t": "garbage"}, follow_redirects=False
        ).status_code
        == 401
    )


def test_an_expired_link_is_refused(client, people, chair):
    expired = create_access_token(
        {
            "sub": people["achari"]["id"],
            "scope": "lab-link",
            "item": chair["id"],
            "mode": "view",
        },
        expires_minutes=-1,
    )
    assert (
        client.get(
            "/lab/open", params={"t": expired}, follow_redirects=False
        ).status_code
        == 401
    )


def test_a_forged_link_for_a_missing_user_is_refused(client, people, chair):
    token = create_scoped_token(
        str(uuid.uuid4()), "lab-link", 10, item=chair["id"], mode="view"
    )
    assert (
        client.get("/lab/open", params={"t": token}, follow_redirects=False).status_code
        == 401
    )


def test_a_token_signed_with_another_key_is_refused(client, people, chair):
    forged = jwt.encode(
        {"sub": people["owner"]["id"], "scope": "lab", "exp": 4102444800},
        "not-the-key",
        algorithm=ALGORITHM,
    )
    client.cookies.set("lab_session", forged, path="/lab")
    assert client.get("/lab/items").status_code == 401
    assert SECRET_KEY != "not-the-key"


def test_only_roles_that_may_view_get_a_link(client, people, chair):
    r = client.post(
        "/lab/links", headers=people["sales"]["h"], json={"item_id": chair["id"]}
    )
    assert r.status_code == 403
    assert client.post("/lab/links", json={"item_id": chair["id"]}).status_code == 401


def test_links_are_only_for_furniture(client, people, chair):
    plain = client.post(
        "/items/",
        headers=people["owner"]["h"],
        json={"sku": f"PL-{uuid.uuid4().hex[:6]}", "name": "Plank"},
    ).json()
    r = client.post(
        "/lab/links", headers=people["owner"]["h"], json={"item_id": plain["id"]}
    )
    assert r.status_code == 404


def test_edit_links_are_for_owner_and_manager_only(client, people, chair):
    r = client.post(
        "/lab/links",
        headers=people["achari"]["h"],
        json={"item_id": chair["id"], "mode": "edit"},
    )
    assert r.status_code == 403
    resp = open_session(client, people, chair, role="manager", mode="edit")
    assert resp.headers["location"] == f"/lab/edit/{chair['id']}"


# ---------------------------------------------------- changing data with the cookie
def test_cookie_edits_need_the_lab_client_header(client, people, chair):
    open_session(client, people, chair, role="owner", mode="edit")
    body = {"parts": chair_parts()[:1]}
    url = f"/lab/items/{chair['id']}/parts"
    # a foreign site cannot add this header, so the browser cannot be tricked into editing
    assert client.put(url, json=body).status_code == 403
    assert client.put(url, json=body, headers={"X-Lab-Client": "1"}).status_code == 200


def test_the_cookie_carries_the_users_role_for_edits(client, people, chair):
    open_session(client, people, chair, role="achari")  # view-only role
    r = client.put(
        f"/lab/items/{chair['id']}/parts",
        json={"parts": chair_parts()[:1]},
        headers={"X-Lab-Client": "1"},
    )
    assert r.status_code == 403
    assert client.get(f"/lab/items/{chair['id']}/definition").status_code == 403


def test_a_deactivated_user_loses_lab_access(client, people, chair, db):
    open_session(client, people, chair, role="worker")
    assert client.get("/lab/items").status_code == 200
    user = db.query(User).filter(User.id == people["worker"]["id"]).one()
    user.is_active = False
    db.commit()
    assert client.get("/lab/items").status_code == 401


def test_page_urls_must_be_uuids(client, people, chair):
    for bad in ("../../etc/passwd", "abc", "x" * 36):
        assert client.get(f"/lab/view/{bad}").status_code == 404
    assert (
        client.get("/lab/static/view.html").status_code == 200
    )  # served, but it holds no data
    assert client.get("/lab/static/../../main.py").status_code in (400, 404)


@pytest.mark.parametrize(
    "path", ["/lab/items", "/lab/items/00000000-0000-0000-0000-000000000000"]
)
def test_lab_data_needs_authentication(client, path):
    assert client.get(path).status_code == 401
