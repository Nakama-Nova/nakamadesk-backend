"""
Updates for the desktop app and the phone app, for signed-in staff only.

GET /app/updates/latest             -> the newest version and its checksum
GET /app/updates/download/{version} -> redirect to a short-lived signed download
GET /app/updates/mobile/latest       -> the newest phone app (.apk) and its checksum
GET /app/updates/mobile/download/{version} -> {"url": a short-lived signed download}

The desktop compares the version itself, downloads, checks the SHA-256 and runs
the installer. See app/services/release_service.py for where this comes from.
"""

import re

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from app.core.config import settings
from app.db.deps import get_current_user
from app.models.user import User
from app.services import release_service

router = APIRouter(prefix="/app/updates", tags=["App updates"])


class LatestRelease(BaseModel):
    version: str
    notes: str
    installer_name: str
    size: int
    sha256: str


@router.get("/latest", response_model=LatestRelease)
def latest_release(_: User = Depends(get_current_user)):
    release = release_service.latest()
    if release is None:
        raise HTTPException(status_code=404, detail="No update has been published yet")
    return release


class LatestMobile(BaseModel):
    version: str
    notes: str
    apk_name: str
    size: int
    sha256: str


class DownloadLink(BaseModel):
    url: str


@router.get("/mobile/latest", response_model=LatestMobile)
def latest_mobile(_: User = Depends(get_current_user)):
    release = release_service.latest_mobile()
    if release is None:
        raise HTTPException(
            status_code=404, detail="No phone app update has been published yet"
        )
    return {**release, "apk_name": release["installer_name"]}


@router.get("/mobile/download/{version}", response_model=DownloadLink)
def download_mobile(version: str, _: User = Depends(get_current_user)):
    """The signed address as JSON: a phone opens it in the browser, which cannot send a login."""
    release = release_service.latest_mobile()
    if (
        release is None
        or not re.fullmatch(r"\d+\.\d+\.\d+", version)
        or version != release["version"]
    ):
        raise HTTPException(status_code=404, detail="That version is not available")
    return {
        "url": release_service.installer_address(
            release["asset_id"], settings.GITHUB_MOBILE_RELEASES_REPO
        )
    }


@router.get("/download/{version}")
def download(version: str, _: User = Depends(get_current_user)):
    """Only the newest version can be downloaded here."""
    release = release_service.latest()
    if (
        release is None
        or not re.fullmatch(r"\d+\.\d+\.\d+", version)
        or version != release["version"]
    ):
        raise HTTPException(status_code=404, detail="That version is not available")
    return RedirectResponse(
        release_service.installer_address(release["asset_id"]),
        status_code=302,
        headers={"Cache-Control": "no-store"},
    )
