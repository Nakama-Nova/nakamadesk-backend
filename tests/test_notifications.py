"""
The notification feed: who is told what, that nothing repeats, and the phone app's update check.
"""

import hashlib
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.core.config import settings
from app.models.order import Order
from app.models.scan_job import ScanJob
from app.services import release_service
from tests.test_order_processing import new_customer, new_order, pay
from tests.test_restoration import new_project

ALL_ROLES = ("owner", "manager", "sales", "achari", "worker", "artisan")


def started():
    return (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat()


def ids(client, h, since):
    r = client.get("/notifications", headers=h, params={"since": since})
    assert r.status_code == 200, r.text
    return {x["id"]: x for x in r.json()["items"]}


def web_order(db, number=None, created=None, amount=4500):
    o = Order(
        order_number=number or f"ORD-T{uuid.uuid4().hex[:8]}",
        order_type="standard",
        status="draft",
        source="web",
        estimated_amount=amount,
    )
    if created:
        o.created_at = created
    db.add(o)
    db.commit()
    db.refresh(o)
    return o


# ------------------------------------------------------------------ the feed
def test_the_first_call_only_says_where_to_start(client, people):
    r = client.get("/notifications", headers=people["owner"]["h"])
    assert r.status_code == 200
    body = r.json()
    assert body["items"] == [] and datetime.fromisoformat(body["server_time"]).tzinfo
    assert client.get("/notifications").status_code == 401


def test_a_website_order_tells_the_people_who_handle_orders(client, people, db):
    since = started()
    order = web_order(db)
    for role in ("owner", "manager", "sales"):
        got = ids(client, people[role]["h"], since)[f"order:{order.id}"]
        assert got["kind"] == "order_web" and order.order_number in got["title"], role
        assert got["screen"] == "orders" and got["target_id"] == str(order.id)
        assert "4,500.00" in got["body"]
    for role in ("achari", "worker", "artisan"):
        assert f"order:{order.id}" not in ids(client, people[role]["h"], since), role


def test_nothing_is_told_twice(client, people, db):
    order = web_order(db)
    first = client.get(
        "/notifications", headers=people["owner"]["h"], params={"since": started()}
    ).json()
    assert f"order:{order.id}" in {x["id"] for x in first["items"]}
    later = client.get(
        "/notifications",
        headers=people["owner"]["h"],
        params={"since": first["server_time"]},
    ).json()
    assert f"order:{order.id}" not in {
        x["id"] for x in later["items"]
    }  # asking from server_time skips it


def test_money_received_is_for_the_owner_and_manager_only(client, people):
    h = people["owner"]["h"]
    order = new_order(client, h, customer=new_customer(client, h), estimate=20000)
    since = started()
    r = pay(client, h, order, 5000, method="upi", reference="UPI42")
    assert r.status_code == 201, r.text
    for role in ("owner", "manager"):
        found = [
            x
            for x in ids(client, people[role]["h"], since).values()
            if x["kind"] == "payment" and order["order_number"] in x["title"]
        ]
        assert (
            len(found) == 1
            and "5,000.00" in found[0]["title"]
            and found[0]["body"] == "UPI, UPI42"
        ), role
        assert found[0]["target_id"] == order["id"]
    assert not [
        x
        for x in ids(client, people["sales"]["h"], since).values()
        if x["kind"] == "payment"
    ]


def test_stock_dropping_to_its_warning_level_is_reported_once(client, people):
    h = people["owner"]["h"]
    item = client.post(
        "/items/",
        headers=h,
        json={
            "name": "Teak stool",
            "selling_price": 500,
            "current_stock": 6,
            "min_stock": 5,
        },
    ).json()
    since = started()
    for _ in range(2):  # two sales, both below the warning level: one notice
        r = client.post(
            "/sales/",
            headers=h,
            json={"items": [{"item_id": item["id"], "quantity": 1}]},
        )
        assert r.status_code == 200, r.text
    low = [
        x
        for x in ids(client, h, since).values()
        if x["kind"] == "low_stock" and x["target_id"] == item["id"]
    ]
    assert (
        len(low) == 1
        and low[0]["title"] == "Low stock: Teak stool"
        and low[0]["body"] == "Only 4 left"
    )
    assert low[0]["screen"] == "stock"
    assert not [
        x
        for x in ids(client, people["sales"]["h"], since).values()
        if x["kind"] == "low_stock"
    ]
    assert not [x for x in ids(client, people["worker"]["h"], since).values()]


def test_a_stock_item_still_above_its_warning_level_is_not_reported(client, people):
    h = people["owner"]["h"]
    item = client.post(
        "/items/",
        headers=h,
        json={
            "name": "Big stock",
            "selling_price": 500,
            "current_stock": 50,
            "min_stock": 5,
        },
    ).json()
    since = started()
    client.post(
        "/sales/", headers=h, json={"items": [{"item_id": item["id"], "quantity": 1}]}
    )
    assert not [
        x for x in ids(client, h, since).values() if x["target_id"] == item["id"]
    ]


def test_a_finished_scan_goes_to_artisans_and_the_owner_not_a_manager_for_restorations(
    client, people, db
):
    owner = people["owner"]["h"]
    project = new_project(client, owner, title=f"Door {uuid.uuid4().hex[:5]}")
    since = started()
    scan = ScanJob(
        restoration_id=project["id"],
        source_kind="photos",
        status="done",
        artisan_only=True,
        finished_at=datetime.now(timezone.utc),
    )
    ordinary = ScanJob(
        source_kind="photos",
        status="failed",
        error="no GPU",
        artisan_only=False,
        finished_at=datetime.now(timezone.utc),
    )
    db.add_all([scan, ordinary])
    db.commit()
    for role in ("owner", "artisan"):
        got = ids(client, people[role]["h"], since)
        done = got[f"scan:{scan.id}:done"]
        assert (
            done["title"] == f"3D model ready: {project['title']}"
            and done["screen"] == "restoration"
        ), role
    assert f"scan:{ordinary.id}:failed" in ids(client, owner, since)
    manager = ids(client, people["manager"]["h"], since)
    assert (
        f"scan:{scan.id}:done" not in manager
        and manager[f"scan:{ordinary.id}:failed"]["body"] == "no GPU"
    )
    for role in ("sales", "worker", "achari"):
        assert not [
            k for k in ids(client, people[role]["h"], since) if k.startswith("scan:")
        ], role


def test_old_events_are_not_replayed(client, people, db):
    old = web_order(
        db, created=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=5)
    )
    week_ago = (datetime.now(timezone.utc) - timedelta(days=9)).isoformat()
    assert f"order:{old.id}" not in ids(
        client, people["owner"]["h"], week_ago
    )  # looked back at most 3 days


def test_a_bad_since_is_refused_and_a_future_one_is_empty(client, people):
    h = people["owner"]["h"]
    assert (
        client.get(
            "/notifications", headers=h, params={"since": "yesterday"}
        ).status_code
        == 422
    )
    future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    assert ids(client, h, future) == {}


# ------------------------------------------------------- phone app updates
APK = "VrikshaCapture-1.1.0.apk"
SUM = hashlib.sha256(b"apk").hexdigest()
SIGNED = "https://objects.githubusercontent.com/signed/apk?sig=1"


class Resp:
    def __init__(self, status=200, data=None, text="", headers=None):
        self.status_code, self._data, self.text, self.headers = (
            status,
            data,
            text,
            headers or {},
        )

    def json(self):
        return self._data


@pytest.fixture
def github(monkeypatch):
    state = {"calls": []}
    monkeypatch.setattr(settings, "GITHUB_RELEASES_TOKEN", "ghp_test")
    monkeypatch.setattr(
        settings, "GITHUB_MOBILE_RELEASES_REPO", "Nakama-Nova/vriksha-mobile"
    )
    release_service.reset()

    def fake_get(url, headers=None, allow_redirects=True, timeout=None):
        state["calls"].append(url)
        assert (
            "/repos/Nakama-Nova/vriksha-mobile/" in url
        )  # the phone app's own repo, not the desktop's
        if url.endswith("/releases/latest"):
            assets = [
                {"id": 21, "name": APK, "size": 24_000_000},
                {"id": 22, "name": APK + ".sha256", "size": 90},
            ]
            return Resp(
                data={"tag_name": "v1.1.0", "body": "Notifications", "assets": assets}
            )
        if url.endswith("/releases/assets/22"):
            return Resp(text=f"{SUM}  {APK}\n")
        if url.endswith("/releases/assets/21"):
            return Resp(302, headers={"Location": SIGNED})
        return Resp(404)

    monkeypatch.setattr(release_service.requests, "get", fake_get)
    yield state
    release_service.reset()


def test_the_phone_app_finds_its_newest_version(client, people, github):
    r = client.get("/app/updates/mobile/latest", headers=people["worker"]["h"])
    assert r.status_code == 200, r.text
    assert r.json() == {
        "version": "1.1.0",
        "notes": "Notifications",
        "apk_name": APK,
        "size": 24_000_000,
        "sha256": SUM,
    }
    assert "ghp_" not in r.text and "asset_id" not in r.text
    assert client.get("/app/updates/mobile/latest").status_code == 401


def test_the_phone_gets_a_signed_address_as_json_for_the_newest_version_only(
    client, people, github
):
    h = people["sales"]["h"]
    r = client.get("/app/updates/mobile/download/1.1.0", headers=h)
    assert r.status_code == 200 and r.json() == {"url": SIGNED}
    for v in ("1.0.0", "latest", "1.1", "../1.1.0"):
        assert (
            client.get(f"/app/updates/mobile/download/{v}", headers=h).status_code
            == 404
        )


def test_the_desktop_and_phone_updates_do_not_mix(client, people, github, monkeypatch):
    # a desktop installer in the phone repo's release is not a phone update
    def only_exe(url, headers=None, allow_redirects=True, timeout=None):
        assets = [{"id": 1, "name": "VrikshaStudiosDesk-Setup-1.1.0.exe", "size": 1}]
        return (
            Resp(data={"tag_name": "v1.1.0", "body": "", "assets": assets})
            if url.endswith("/releases/latest")
            else Resp(404)
        )

    monkeypatch.setattr(release_service.requests, "get", only_exe)
    release_service.reset()
    assert (
        client.get(
            "/app/updates/mobile/latest", headers=people["owner"]["h"]
        ).status_code
        == 404
    )
