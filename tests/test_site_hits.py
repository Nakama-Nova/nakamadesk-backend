"""The website's anonymous visitor counts: one number per day, page or action, nothing about the visitor."""

import uuid

import pytest
from sqlalchemy import inspect

from app.core import ratelimit
from app.models.site_content import SiteHit
from app.services.site_service import record_hit, stats


@pytest.fixture(autouse=True)
def _fresh():
    ratelimit.reset()
    yield
    ratelimit.reset()


def page():
    return f"/t-{uuid.uuid4().hex[:10]}"


def count(db, kind, key):
    row = db.query(SiteHit).filter(SiteHit.kind == kind, SiteHit.key == key).first()
    return row.count if row else 0


def test_views_are_counted_per_page_without_any_login(auth_client, db):
    p = page()
    for _ in range(3):
        r = auth_client.post("/public/hit", json={"kind": "view", "key": p})
        assert r.status_code == 202 and r.json() == {"ok": True}
    assert count(db, "view", p) == 3


def test_a_query_string_or_trailing_slash_does_not_make_a_new_page(auth_client, db):
    p = page()
    auth_client.post("/public/hit", json={"kind": "view", "key": f"{p}/?utm=x#top"})
    auth_client.post("/public/hit", json={"kind": "view", "key": p})
    assert count(db, "view", p) == 2


def test_only_the_known_actions_are_counted(auth_client, db):
    before = count(db, "event", "add_to_cart")
    auth_client.post("/public/hit", json={"kind": "event", "key": "add_to_cart"})
    auth_client.post("/public/hit", json={"kind": "event", "key": "make_me_rich"})
    assert count(db, "event", "add_to_cart") == before + 1
    assert count(db, "event", "make_me_rich") == 0


@pytest.mark.parametrize(
    "junk",
    [
        "/ spaced",
        "/<script>",
        "no-slash",
        "/" + "a" * 130,
        "https://evil.example/x",
        "/UPPER?x=1" + "\n",
    ],
)
def test_junk_page_addresses_are_ignored_but_answered_the_same(auth_client, db, junk):
    r = auth_client.post("/public/hit", json={"kind": "view", "key": junk[:120]})
    assert r.status_code == 202
    keys = [h.key for h in db.query(SiteHit).filter(SiteHit.kind == "view").all()]
    assert not any(
        "<script>" in k or "evil" in k or " " in k or "\n" in k for k in keys
    )


def test_robots_are_not_counted(auth_client, db):
    p = page()
    auth_client.post(
        "/public/hit",
        json={"kind": "view", "key": p},
        headers={"User-Agent": "Googlebot/2.1"},
    )
    auth_client.post(
        "/public/hit",
        json={"kind": "view", "key": p},
        headers={"User-Agent": "Mozilla/5.0 (iPhone)"},
    )
    assert count(db, "view", p) == 1


def test_nothing_that_identifies_a_visitor_is_stored(db):
    cols = {c["name"] for c in inspect(db.get_bind()).get_columns("site_hits")}
    assert cols == {"day", "kind", "key", "count"}


def test_staff_see_totals_top_pages_and_actions(auth_client, db):
    p = page()
    before = stats(db, 30)
    for _ in range(4):
        record_hit(db, "view", p)
    record_hit(db, "event", "order_placed")
    r = auth_client.get("/site/stats", params={"days": 30})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["views"] == before.views + 4
    assert {"key": p, "count": 4} in out["pages"]
    assert any(e["key"] == "order_placed" for e in out["events"])
    assert out["per_day"][-1]["views"] >= 4


def test_stats_are_for_staff_only(client, worker_client):
    assert worker_client.get("/site/stats").status_code == 403


def test_stats_need_a_login(client):
    assert client.get("/site/stats").status_code == 401


def test_the_counter_has_its_own_generous_limit(auth_client):
    p = page()
    codes = [
        auth_client.post("/public/hit", json={"kind": "view", "key": p}).status_code
        for _ in range(121)
    ]
    assert codes.count(202) == 120 and codes[-1] == 429
