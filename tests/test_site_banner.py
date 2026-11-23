"""The home-page banner: a headline with a picture or a YouTube / Instagram video."""

import io
from datetime import timedelta

import pytest
from PIL import Image

from app.core import ratelimit
from app.core.config import settings
from app.schemas.site import read_video_link
from app.services.site_service import today_india


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    ratelimit.reset()
    monkeypatch.setattr(settings, "PUBLIC_RATE_LIMIT", 100)
    yield
    ratelimit.reset()


def save(c, **over):
    body = {
        "headline": "Wedding season beds",
        "media_kind": "none",
        "enabled": True,
        **over,
    }
    return c.put("/site/banner", json=body)


def png(size=(64, 36)):
    b = io.BytesIO()
    Image.new("RGB", size, "teal").save(b, "PNG")
    return b.getvalue()


@pytest.mark.parametrize(
    "kind,link,expected",
    [
        ("youtube", "https://youtu.be/dQw4w9WgXcQ", ("dQw4w9WgXcQ", None)),
        (
            "youtube",
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=5s",
            ("dQw4w9WgXcQ", None),
        ),
        (
            "youtube",
            "https://youtube.com/shorts/dQw4w9WgXcQ?feature=share",
            ("dQw4w9WgXcQ", None),
        ),
        ("youtube", "https://www.youtube.com/live/dQw4w9WgXcQ", ("dQw4w9WgXcQ", None)),
        ("youtube", "https://evil.example/watch?v=dQw4w9WgXcQ", None),
        ("youtube", "https://www.youtube.com.evil.example/watch?v=dQw4w9WgXcQ", None),
        ("youtube", "https://www.youtube.com/watch?v=short", None),
        (
            "instagram",
            "https://www.instagram.com/reel/C8abcDEF123/?igsh=xyz",
            ("C8abcDEF123", "reel"),
        ),
        (
            "instagram",
            "https://instagram.com/vrikshastudios/reel/C8abcDEF123/",
            ("C8abcDEF123", "reel"),
        ),
        ("instagram", "https://www.instagram.com/p/C8abcDEF123/", ("C8abcDEF123", "p")),
        ("instagram", "https://instagram.com.evil.example/reel/C8abcDEF123/", None),
        ("instagram", "javascript:alert(1)", None),
    ],
)
def test_only_real_video_links_are_understood(kind, link, expected):
    assert read_video_link(kind, link) == expected


def test_a_youtube_banner_keeps_only_the_id_and_builds_the_embed_itself(auth_client):
    r = save(
        auth_client,
        media_kind="youtube",
        media_link="https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=junk",
        button_url="/shop",
        button_label="See beds",
        body="Free setup on every bed this month",
    )
    assert r.status_code == 200, r.text
    assert r.json()["showing_now"] is True
    shown = auth_client.get("/public/banner").json()
    assert shown["media_kind"] == "youtube"
    assert (
        shown["embed_url"]
        == "https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ?rel=0&playsinline=1"
    )
    assert shown["watch_url"] == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    assert shown["button_url"] == "/shop" and shown["button_label"] == "See beds"
    assert shown["image_url"] is None


def test_an_instagram_reel_banner(auth_client):
    save(
        auth_client,
        media_kind="instagram",
        media_link="https://www.instagram.com/reel/C8abcDEF123/?igsh=1",
    )
    shown = auth_client.get("/public/banner").json()
    assert shown["embed_url"] == "https://www.instagram.com/reel/C8abcDEF123/embed/"
    assert shown["watch_url"] == "https://www.instagram.com/reel/C8abcDEF123/"


def test_a_wrong_link_for_the_chosen_kind_is_refused(auth_client):
    assert (
        save(
            auth_client,
            media_kind="youtube",
            media_link="https://www.instagram.com/reel/C8abcDEF123/",
        ).status_code
        == 422
    )
    assert (
        save(
            auth_client,
            media_kind="instagram",
            media_link="https://youtu.be/dQw4w9WgXcQ",
        ).status_code
        == 422
    )
    assert save(auth_client, media_kind="youtube", media_link=None).status_code == 422
    assert (
        save(
            auth_client, media_kind="none", button_url="javascript:alert(1)"
        ).status_code
        == 422
    )


def test_the_banner_shows_only_while_it_is_live(auth_client):
    today = today_india()
    save(auth_client)
    assert auth_client.get("/public/banner").json()["headline"] == "Wedding season beds"
    save(auth_client, enabled=False)
    assert auth_client.get("/public/banner").json() is None
    save(auth_client, starts_on=str(today + timedelta(days=1)))
    assert auth_client.get("/public/banner").json() is None
    save(auth_client, ends_on=str(today - timedelta(days=1)))
    assert auth_client.get("/public/banner").json() is None
    save(auth_client, starts_on=str(today), ends_on=str(today))
    assert auth_client.get("/public/banner").json() is not None
    assert (
        save(
            auth_client, starts_on=str(today), ends_on=str(today - timedelta(days=1))
        ).status_code
        == 422
    )


def test_a_picture_banner_uploads_shrinks_and_replaces(auth_client, store):
    save(auth_client, media_kind="image")

    r = auth_client.post(
        "/site/banner/image", files={"file": ("a.png", png(), "image/png")}
    )
    assert r.status_code == 200, r.text
    url = r.json()["image_url"]
    assert url.startswith("/media/public/") and r.json()["media_kind"] == "image"
    shown = auth_client.get("/public/banner").json()
    assert shown["media_kind"] == "image" and shown["image_url"] == url
    assert url.removeprefix("/media/") in store.objects

    again = auth_client.post(
        "/site/banner/image", files={"file": ("b.png", png((80, 45)), "image/png")}
    )
    assert again.status_code == 200
    new = again.json()["image_url"]
    assert (
        new != url and url.removeprefix("/media/") not in store.objects
    )  # the old file is gone


def test_a_banner_picture_must_be_a_real_image(auth_client, store):
    save(auth_client, media_kind="image")
    bad = auth_client.post(
        "/site/banner/image", files={"file": ("x.png", b"not an image", "image/png")}
    )
    assert bad.status_code == 400
    wrong_type = auth_client.post(
        "/site/banner/image", files={"file": ("x.txt", b"hello", "text/plain")}
    )
    assert wrong_type.status_code == 415


def test_workers_cannot_edit_the_banner(worker_client):
    assert worker_client.get("/site/banner").status_code == 403
    assert (
        worker_client.put("/site/banner", json={"headline": "Hello there"}).status_code
        == 403
    )
    assert (
        worker_client.post(
            "/site/banner/image", files={"file": ("a.png", png(), "image/png")}
        ).status_code
        == 403
    )


def test_banner_editing_needs_a_login(client):
    assert (
        client.put("/site/banner", json={"headline": "Hello there"}).status_code == 401
    )
