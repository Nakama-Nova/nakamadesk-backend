"""The website's top-bar announcement and the ribbons on product photos."""

import uuid
from datetime import timedelta

import pytest

from app.core import ratelimit
from app.core.config import settings
from app.services.site_service import today_india
from tests.test_public_api import make_item


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    ratelimit.reset()
    monkeypatch.setattr(settings, "PUBLIC_RATE_LIMIT", 100)
    yield
    ratelimit.reset()


def save(c, **over):
    body = {"message": "Free setup on every bed this week", "enabled": True, **over}
    return c.put("/site/announcement", json=body)


def test_the_announcement_shows_only_while_it_is_live(auth_client):
    today = today_india()
    r = save(auth_client, link_url="/shop", link_label="See beds")
    assert r.status_code == 200, r.text
    assert r.json()["showing_now"] is True
    assert auth_client.get("/public/announcement").json() == {
        "message": "Free setup on every bed this week",
        "link_url": "/shop",
        "link_label": "See beds",
    }
    # switched off
    assert save(auth_client, enabled=False).json()["showing_now"] is False
    assert auth_client.get("/public/announcement").json() is None
    # not started yet
    save(auth_client, starts_on=str(today + timedelta(days=1)))
    assert auth_client.get("/public/announcement").json() is None
    # already over
    save(auth_client, ends_on=str(today - timedelta(days=1)))
    assert auth_client.get("/public/announcement").json() is None
    # last day still shows, and a link with no words gets a default
    save(
        auth_client,
        starts_on=str(today),
        ends_on=str(today),
        link_url="https://example.com/offer",
    )
    shown = auth_client.get("/public/announcement").json()
    assert (
        shown["message"].startswith("Free setup")
        and shown["link_label"] == "Learn more"
    )


def test_only_one_announcement_is_kept_and_edited(auth_client, db):
    from app.models.site_content import SiteAnnouncement

    save(auth_client, message="First line here")
    save(auth_client, message="Second line here")
    assert db.query(SiteAnnouncement).count() == 1


@pytest.mark.parametrize(
    "link",
    [
        "javascript:alert(1)",
        "//evil.example/x",
        "http://example.com",
        "data:text/html,hi",
        "/shop x",
    ],
)
def test_unsafe_links_are_refused(auth_client, link):
    assert save(auth_client, link_url=link).status_code == 422


def test_dates_must_be_in_order_and_text_is_limited(auth_client):
    today = today_india()
    assert (
        save(
            auth_client, starts_on=str(today), ends_on=str(today - timedelta(days=2))
        ).status_code
        == 422
    )
    assert save(auth_client, message="x" * 161).status_code == 422
    assert save(auth_client, message="ab").status_code == 422


def test_a_ribbon_travels_with_the_product_and_can_be_removed(auth_client):
    name = f"Ribbon Chair {uuid.uuid4().hex[:8]}"
    item = make_item(auth_client, name=name)
    rows = auth_client.get("/site/ribbons").json()
    assert any(r["item_id"] == item["id"] and r["ribbon"] is None for r in rows)

    ok = auth_client.put(f"/site/ribbons/{item['id']}", json={"ribbon": "new"})
    assert ok.status_code == 200 and ok.json()["ribbon"] == "new"
    assert auth_client.get(f"/public/catalog/{item['slug']}").json()["ribbon"] == "new"
    listed = auth_client.get("/public/catalog", params={"q": name}).json()
    assert next(p for p in listed if p["slug"] == item["slug"])["ribbon"] == "new"

    auth_client.put(f"/site/ribbons/{item['id']}", json={"ribbon": "offer"})
    assert (
        auth_client.get(f"/public/catalog/{item['slug']}").json()["ribbon"] == "offer"
    )

    gone = auth_client.put(f"/site/ribbons/{item['id']}", json={"ribbon": None})
    assert gone.status_code == 200
    assert auth_client.get(f"/public/catalog/{item['slug']}").json()["ribbon"] is None


def test_unknown_ribbons_and_items_are_refused(auth_client):
    item = make_item(auth_client)
    assert (
        auth_client.put(
            f"/site/ribbons/{item['id']}", json={"ribbon": "huge-sale"}
        ).status_code
        == 422
    )
    missing = auth_client.put(
        "/site/ribbons/00000000-0000-0000-0000-000000000001", json={"ribbon": "new"}
    )
    assert missing.status_code == 404


def test_workers_cannot_edit_the_website(worker_client):
    assert worker_client.get("/site/announcement").status_code == 403
    assert (
        worker_client.put(
            "/site/announcement", json={"message": "Hello there"}
        ).status_code
        == 403
    )
    assert worker_client.get("/site/ribbons").status_code == 403


def test_editing_needs_a_login(client):
    assert (
        client.put("/site/announcement", json={"message": "Hello there"}).status_code
        == 401
    )


def test_the_admin_page_is_served_with_a_tight_policy(client):
    r = client.get("/site/admin")
    assert r.status_code == 200 and "Website content" in r.text
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert r.headers["cache-control"] == "no-store"
