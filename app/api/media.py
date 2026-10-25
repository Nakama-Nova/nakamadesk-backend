"""Serves uploaded product photos and models at /media/<storage_key>."""

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.core.config import settings

router = APIRouter(tags=["Media"])

_TYPES = {
    ".jpg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".glb": "model/gltf-binary",
}


@router.api_route("/media/{key:path}", methods=["GET", "HEAD"], include_in_schema=False)
def get_media(key: str):
    # Resolve and require the file to sit under MEDIA_DIR (no ../ escapes).
    base = Path(settings.MEDIA_DIR).resolve()
    path = (base / key).resolve()
    media_type = _TYPES.get(path.suffix.lower())
    if base not in path.parents or not path.is_file() or media_type is None:
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(
        path,
        media_type=media_type,
        headers={
            # keys contain a random id, so a given URL never changes
            "Cache-Control": "public, max-age=31536000, immutable",
            "X-Content-Type-Options": "nosniff",
        },
    )
