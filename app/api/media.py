"""Serves uploaded product photos and models at /media/<storage_key>."""

from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, Response

from app.core.config import settings
from app.services.media_service import MAX_BYTES, PUBLIC_PREFIX
from app.services.object_store import S3Store, get_store_or_none

router = APIRouter(tags=["Media"])

_TYPES = {
    ".jpg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".glb": "model/gltf-binary",
}


@router.api_route("/media/{key:path}", methods=["GET", "HEAD"], include_in_schema=False)
def get_media(key: str, store: Optional[S3Store] = Depends(get_store_or_none)):
    media_type = _TYPES.get(Path(key).suffix.lower())
    if media_type is None or ".." in key.split("/"):
        raise HTTPException(status_code=404, detail="Not found")
    headers = {
        # keys contain a random id, so a given URL never changes; s-maxage lets the CDN
        # keep it, so the store is read once per file rather than once per visitor
        "Cache-Control": "public, max-age=31536000, s-maxage=31536000, immutable",
        "X-Content-Type-Options": "nosniff",
    }
    # Public files kept in the object store (only the public/ prefix is ever served)
    if key.startswith(PUBLIC_PREFIX) and store is not None:
        try:
            data = store.get_bytes(key, max(MAX_BYTES.values()))
        except KeyError:
            raise HTTPException(status_code=404, detail="Not found") from None
        return Response(content=data, media_type=media_type, headers=headers)
    # Otherwise a file on this server's disk: resolve it and require it to sit under
    # MEDIA_DIR (no ../ escapes).
    base = Path(settings.MEDIA_DIR).resolve()
    path = (base / key).resolve()
    if base not in path.parents or not path.is_file():
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(path, media_type=media_type, headers=headers)
