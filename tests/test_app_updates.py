"""Desktop updates served from the backend: GitHub is faked, the rules are real."""

import hashlib

import pytest

from app.core.config import settings
from app.services import release_service

NAME = "VrikshaStudiosDesk-Setup-1.2.0.exe"
SUM = hashlib.sha256(b"installer").hexdigest()
SIGNED = "https://objects.githubusercontent.com/signed/abc?sig=1"


class Resp:
    def __init__(self, status=200, data=None, text="", headers=None):
        self.status_code, self._data, self.text = status, data, text
        self.headers = headers or {}

    def json(self):
        return self._data


def release(tag="v1.2.0", with_files=True):
    assets = []
    if with_files:
        assets = [
            {"id": 11, "name": NAME, "size": 31_000_000},
            {"id": 12, "name": NAME + ".sha256", "size": 100},
        ]
    return {"tag_name": tag, "body": "Faster stock screen", "assets": assets}


@pytest.fixture
def github(monkeypatch):
    """Fake GitHub. `state` lets a test change what it answers."""
    state = {
        "release": Resp(data=release()),
        "calls": [],
        "download": Resp(302, headers={"Location": SIGNED}),
    }
    monkeypatch.setattr(settings, "GITHUB_RELEASES_TOKEN", "ghp_test_token")
    monkeypatch.setattr(settings, "GITHUB_RELEASES_REPO", "Nakama-Nova/nakama-desktop")
    release_service.reset()

    def fake_get(url, headers=None, allow_redirects=True, timeout=None):
        state["calls"].append((url, headers, allow_redirects))
        assert headers["Authorization"] == "Bearer ghp_test_token"
        if url.endswith("/releases/latest"):
            return state["release"]
        if url.endswith("/releases/assets/12"):
            return Resp(text=f"{SUM}  {NAME}\n")
        if url.endswith("/releases/assets/11"):
            return state["download"]
        return Resp(404)

    monkeypatch.setattr(release_service.requests, "get", fake_get)
    yield state
    release_service.reset()


def test_staff_see_the_newest_version_with_its_checksum(client, people, github):
    r = client.get("/app/updates/latest", headers=people["worker"]["h"])
    assert r.status_code == 200, r.text
    assert r.json() == {
        "version": "1.2.0",
        "notes": "Faster stock screen",
        "installer_name": NAME,
        "size": 31_000_000,
        "sha256": SUM,
    }
    assert "asset_id" not in r.json() and "ghp_" not in r.text  # nothing internal leaks


def test_every_role_can_check_but_a_login_is_required(client, people, github):
    assert client.get("/app/updates/latest").status_code == 401
    assert (
        client.get("/app/updates/download/1.2.0", follow_redirects=False).status_code
        == 401
    )
    for role in ("owner", "manager", "sales", "achari", "worker"):
        assert (
            client.get("/app/updates/latest", headers=people[role]["h"]).status_code
            == 200
        )


def test_download_redirects_to_a_short_lived_signed_address(client, people, github):
    r = client.get(
        "/app/updates/download/1.2.0",
        headers=people["sales"]["h"],
        follow_redirects=False,
    )
    assert r.status_code == 302 and r.headers["location"] == SIGNED
    assert r.headers["cache-control"] == "no-store"
    # the server asked GitHub without following redirects, so the token never goes to the signed host
    assert github["calls"][-1][2] is False


def test_only_the_newest_version_can_be_downloaded(client, people, github):
    h = people["owner"]["h"]
    for v in ("1.1.0", "9.9.9", "latest", "1.2", "../1.2.0"):
        assert (
            client.get(
                f"/app/updates/download/{v}", headers=h, follow_redirects=False
            ).status_code
            == 404
        )


def test_the_answer_is_cached_so_github_is_not_asked_every_time(client, people, github):
    h = people["owner"]["h"]
    for _ in range(3):
        client.get("/app/updates/latest", headers=h)
    assert sum(1 for c in github["calls"] if c[0].endswith("/releases/latest")) == 1


def test_no_release_or_no_installer_means_no_update(client, people, github):
    h = people["owner"]["h"]
    github["release"] = Resp(404)
    assert client.get("/app/updates/latest", headers=h).status_code == 404
    release_service.reset()
    github["release"] = Resp(
        data=release(with_files=False)
    )  # a release without the installer files
    assert client.get("/app/updates/latest", headers=h).status_code == 404
    release_service.reset()
    github["release"] = Resp(data=release(tag="nightly"))  # not a version
    assert client.get("/app/updates/latest", headers=h).status_code == 404


def test_source_problems_are_clear_not_crashes(client, people, github, monkeypatch):
    h = people["owner"]["h"]
    release_service.reset()
    github["release"] = Resp(403)
    r = client.get("/app/updates/latest", headers=h)
    assert r.status_code == 502 and "token" in r.json()["detail"]
    release_service.reset()
    github["release"] = Resp(500)
    r = client.get("/app/updates/latest", headers=h)
    assert r.status_code == 502 and "error" in r.json()["detail"]


def anonymous_github(github, monkeypatch, seen):
    """No token: the repos are public, so GitHub must be asked without an Authorization header."""
    monkeypatch.setattr(settings, "GITHUB_RELEASES_TOKEN", "")
    release_service.reset()

    def fake_get(url, headers=None, allow_redirects=True, timeout=None):
        seen.append(headers)
        if url.endswith("/releases/latest"):
            return github["release"]
        if url.endswith("/releases/assets/12"):
            return Resp(text=f"{SUM}  {NAME}\n")
        if url.endswith("/releases/assets/11"):
            return github["download"]
        return Resp(404)

    monkeypatch.setattr(release_service.requests, "get", fake_get)


def test_without_a_token_updates_work_by_asking_github_anonymously(
    client, people, github, monkeypatch
):
    seen = []
    anonymous_github(github, monkeypatch, seen)
    h = people["worker"]["h"]
    r = client.get("/app/updates/latest", headers=h)
    assert r.status_code == 200 and r.json()["version"] == "1.2.0"
    d = client.get("/app/updates/download/1.2.0", headers=h, follow_redirects=False)
    assert d.status_code == 302 and d.headers["location"] == SIGNED
    assert seen and all("Authorization" not in x for x in seen)


def test_without_a_token_answers_are_kept_longer_to_stay_under_githubs_limit(
    client, people, github, monkeypatch
):
    seen = []
    anonymous_github(github, monkeypatch, seen)
    assert release_service._cache_seconds() == release_service.ANONYMOUS_CACHE_SECONDS
    monkeypatch.setattr(settings, "GITHUB_RELEASES_TOKEN", "ghp_test_token")
    assert release_service._cache_seconds() == release_service.CACHE_SECONDS


def test_without_a_token_a_refusal_does_not_blame_a_token(
    client, people, github, monkeypatch
):
    anonymous_github(github, monkeypatch, [])
    github["release"] = Resp(403)  # GitHub's rate limit for anonymous questions
    r = client.get("/app/updates/latest", headers=people["owner"]["h"])
    assert r.status_code == 502
    assert "token" not in r.json()["detail"] and "try again" in r.json()["detail"]


def test_a_bad_checksum_file_is_refused(client, people, github, monkeypatch):
    real = release_service.requests.get

    def broken(url, **kw):
        if url.endswith("/assets/12"):
            return Resp(text="not a hash")
        return real(url, **kw)

    monkeypatch.setattr(release_service.requests, "get", broken)
    r = client.get("/app/updates/latest", headers=people["owner"]["h"])
    assert r.status_code == 502 and "checksum" in r.json()["detail"]
