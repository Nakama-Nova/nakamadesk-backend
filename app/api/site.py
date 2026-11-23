"""
Staff side of the website's own content: the announcement in the top bar and the ribbons
(new, bestseller...) on product photos. OWNER and MANAGER only.

`/site/admin` is a small page for editing both in a browser: it asks for the staff login,
keeps the session in the page only (nothing is stored), and talks to the routes below.
"""

from pathlib import Path
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, File, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.db.deps import check_role, get_db, get_uow
from app.models.enums import UserRole
from app.models.user import User
from app.repositories.base import AbstractUnitOfWork
from app.schemas.site import (
    AnnouncementIn,
    AnnouncementOut,
    BannerIn,
    BannerOut,
    RibbonIn,
    RibbonRow,
)
from app.services import media_service, site_service
from app.services.object_store import S3Store, get_store_or_none

router = APIRouter(prefix="/site", tags=["Website content"])

STAFF = [UserRole.OWNER, UserRole.MANAGER]
PAGE = Path(__file__).resolve().parent.parent / "static" / "site" / "admin.html"
PAGE_HEADERS = {
    "Cache-Control": "no-store",
    "Content-Security-Policy": "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
}


@router.get("/admin", include_in_schema=False)
def admin_page():
    """The page for editing the announcement and ribbons (it logs in with the staff login)."""
    return FileResponse(PAGE, media_type="text/html", headers=PAGE_HEADERS)


@router.get("/announcement", response_model=Optional[AnnouncementOut])
def get_announcement(
    current_user: User = Depends(check_role(STAFF)), db: Session = Depends(get_db)
):
    """The saved announcement (even when it is switched off or not showing now), or null."""
    return site_service.staff_announcement(db)


@router.put("/announcement", response_model=AnnouncementOut)
def put_announcement(
    body: AnnouncementIn,
    current_user: User = Depends(check_role(STAFF)),
    db: Session = Depends(get_db),
):
    """Save the announcement. Switch it off with enabled=false; it also ends by itself after ends_on."""
    return site_service.save_announcement(db, body, current_user.id)


@router.get("/ribbons", response_model=List[RibbonRow])
def get_ribbons(
    current_user: User = Depends(check_role(STAFF)),
    uow: AbstractUnitOfWork = Depends(get_uow),
):
    """Every published product with its ribbon (if any)."""
    return site_service.list_ribbon_rows(uow)


@router.put("/ribbons/{item_id}", response_model=RibbonRow)
def put_ribbon(
    item_id: UUID,
    body: RibbonIn,
    current_user: User = Depends(check_role(STAFF)),
    uow: AbstractUnitOfWork = Depends(get_uow),
):
    """Set a product's ribbon (new, bestseller, few_left, offer), or null to remove it."""
    return site_service.set_ribbon(uow, item_id, body.ribbon)


@router.get("/banner", response_model=Optional[BannerOut])
def get_banner(
    current_user: User = Depends(check_role(STAFF)), db: Session = Depends(get_db)
):
    """The saved home-page banner (even when switched off or not showing now), or null."""
    return site_service.staff_banner(db)


@router.put("/banner", response_model=BannerOut)
def put_banner(
    body: BannerIn,
    current_user: User = Depends(check_role(STAFF)),
    db: Session = Depends(get_db),
):
    """
    Save the banner. For a video, paste its YouTube link or Instagram reel link in `media_link`:
    only the video's id is kept. A picture is added with POST /site/banner/image after saving.
    """
    return site_service.save_banner(db, body, current_user.id)


@router.post("/banner/image", response_model=BannerOut)
async def post_banner_image(
    file: UploadFile = File(...),
    current_user: User = Depends(check_role(STAFF)),
    db: Session = Depends(get_db),
    store: Optional[S3Store] = Depends(get_store_or_none),
):
    """Upload the banner's picture (JPG, PNG or WebP, 8 MB max). It is shrunk to web size."""
    data = await file.read(media_service.max_read_size())
    return site_service.save_banner_image(
        db, file.content_type or "", data, store, current_user.id
    )
