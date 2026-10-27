"""
Where the desktop app finds its updates.

The installers are published as GitHub Releases of a PRIVATE repository, so
the app cannot read them itself. This server reads them with a read-only token
(GITHUB_RELEASES_TOKEN) and passes them on only to signed-in staff: it tells the
app what the newest version is, and sends it to a short-lived signed download
address. The token never leaves the server.
"""

import re
import time
from typing import Optional

import requests
from fastapi import HTTPException

from app.core.config import settings

API = "https://api.github.com"
INSTALLER = re.compile(r"^VrikshaStudiosDesk-Setup-[0-9A-Za-z.\-]+\.exe$")
VERSION = re.compile(r"^v?(\d+\.\d+\.\d+)$")
CACHE_SECONDS = 300
TIMEOUT = 10

_cache: dict = {"at": 0.0, "value": None}


def reset() -> None:
    """Forget the cached answer (used by tests)."""
    _cache.update(at=0.0, value=None)


def _headers(accept: str = "application/vnd.github+json") -> dict:
    return {
        "Authorization": f"Bearer {settings.GITHUB_RELEASES_TOKEN}",
        "Accept": accept,
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _need_token() -> None:
    if not settings.GITHUB_RELEASES_TOKEN:
        raise HTTPException(
            status_code=503, detail="Updates are not set up on this server yet"
        )


def _get(url: str, **kwargs):
    try:
        return requests.get(url, timeout=TIMEOUT, **kwargs)
    except requests.RequestException:
        raise HTTPException(
            status_code=502, detail="Could not reach the update source"
        ) from None


def _read_checksum(asset_id: int) -> str:
    """The SHA-256 published next to the installer ('<hash>  <file name>')."""
    resp = _get(
        f"{API}/repos/{settings.GITHUB_RELEASES_REPO}/releases/assets/{asset_id}",
        headers=_headers("application/octet-stream"),
    )
    token = (
        resp.text.split()[0].lower() if resp.status_code == 200 and resp.text else ""
    )
    if not re.fullmatch(r"[0-9a-f]{64}", token):
        raise HTTPException(status_code=502, detail="The update has no valid checksum")
    return token


def latest() -> Optional[dict]:
    """The newest published installer, or None. Cached for a few minutes."""
    _need_token()
    now = time.monotonic()
    if _cache["value"] is not None and now - _cache["at"] < CACHE_SECONDS:
        return _cache["value"]

    resp = _get(
        f"{API}/repos/{settings.GITHUB_RELEASES_REPO}/releases/latest",
        headers=_headers(),
    )
    if resp.status_code == 404:
        return None  # nothing published yet
    if resp.status_code in (401, 403):
        raise HTTPException(
            status_code=502, detail="The update token was refused (it may have expired)"
        )
    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail="The update source gave an error")

    release = resp.json()
    match = VERSION.match(release.get("tag_name") or "")
    assets = {a["name"]: a for a in release.get("assets", [])}
    installer = next((a for n, a in assets.items() if INSTALLER.match(n)), None)
    checksum = assets.get(f"{installer['name']}.sha256") if installer else None
    if not match or not installer or not checksum:
        return None  # a release without the installer files is not an update

    value = {
        "version": match.group(1),
        "notes": (release.get("body") or "").strip()[:2000],
        "installer_name": installer["name"],
        "size": int(installer.get("size") or 0),
        "sha256": _read_checksum(checksum["id"]),
        "asset_id": installer["id"],
    }
    _cache.update(at=now, value=value)
    return value


def installer_address(asset_id: int) -> str:
    """A short-lived signed address for the installer (valid for minutes)."""
    _need_token()
    resp = _get(
        f"{API}/repos/{settings.GITHUB_RELEASES_REPO}/releases/assets/{asset_id}",
        headers=_headers("application/octet-stream"),
        allow_redirects=False,
    )
    location = resp.headers.get("Location")
    if resp.status_code not in (301, 302, 307) or not location:
        raise HTTPException(status_code=502, detail="The installer is not available")
    return location
