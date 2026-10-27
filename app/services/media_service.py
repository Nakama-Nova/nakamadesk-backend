"""
Product photo / 3D model storage.

Files live under settings.MEDIA_DIR and are served at /media/<storage_key>.
Only this module knows that, so moving to R2/S3 later means changing save(),
delete() and url() here and nothing else.
"""

import io
import re
import uuid
from pathlib import Path
from typing import Tuple

from fastapi import HTTPException
from PIL import Image, UnidentifiedImageError

from app.core.config import settings

# content type -> (kind, file extension)
ALLOWED = {
    "image/jpeg": ("image", "jpg"),
    "image/png": ("image", "png"),
    "image/webp": ("image", "webp"),
    "model/gltf-binary": ("glb", "glb"),
}
MAX_BYTES = {"image": 10 * 1024 * 1024, "glb": 25 * 1024 * 1024}
_PIL_FORMAT = {"jpg": "JPEG", "png": "PNG", "webp": "WEBP"}


def max_read_size() -> int:
    """Most bytes worth reading from an upload before it is surely too big."""
    return max(MAX_BYTES.values()) + 1


def validate(content_type: str, data: bytes) -> Tuple[str, str]:
    """
    Check an upload and return (kind, extension). The declared content type is
    not trusted: images must really decode as that format and a .glb must start
    with the glTF magic bytes.
    """
    if content_type not in ALLOWED:
        raise HTTPException(
            status_code=415, detail="Only JPG, PNG, WebP images or .glb models"
        )
    kind, ext = ALLOWED[content_type]
    if not data:
        raise HTTPException(status_code=400, detail="Empty file")
    if len(data) > MAX_BYTES[kind]:
        raise HTTPException(
            status_code=413,
            detail=f"File too large (max {MAX_BYTES[kind] // (1024 * 1024)} MB)",
        )
    if kind == "image":
        try:
            with Image.open(io.BytesIO(data)) as img:
                fmt = img.format
                img.verify()
        except (UnidentifiedImageError, OSError, ValueError):
            raise HTTPException(status_code=400, detail="Not a valid image") from None
        if fmt != _PIL_FORMAT[ext]:
            raise HTTPException(
                status_code=400, detail="File content does not match its type"
            )
    elif data[:4] != b"glTF":
        raise HTTPException(status_code=400, detail="Not a valid .glb file")
    return kind, ext


def save(item_id: uuid.UUID, ext: str, data: bytes) -> str:
    """Write the file and return its storage_key (relative path)."""
    key = f"items/{item_id}/{uuid.uuid4().hex}.{ext}"
    path = Path(settings.MEDIA_DIR) / key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return key


def delete(storage_key: str) -> None:
    try:
        (Path(settings.MEDIA_DIR) / storage_key).unlink(missing_ok=True)
    except OSError:
        pass  # a stray file is harmless; never fail the request over it


def url(storage_key: str) -> str:
    return f"/media/{storage_key}"


# ---------------------------------------------------------------------------
# Files that go to the private object store (presigned upload, not via the API)
# ---------------------------------------------------------------------------
MB = 1024 * 1024

# kind -> (allowed extensions, max bytes, may be shown on the website?)
PRIVATE_KINDS = {
    "render": ({".jpg", ".jpeg", ".png", ".webp"}, 15 * MB, True),
    "glb": ({".glb"}, 100 * MB, True),
    "part_model": ({".glb"}, 50 * MB, False),
    "stl": ({".stl"}, 300 * MB, False),
    "obj": ({".obj"}, 300 * MB, False),
    "ply": ({".ply"}, 300 * MB, False),
    "nc": ({".nc", ".gcode", ".ngc", ".tap"}, 50 * MB, False),
    "source_scan": (
        {".mp4", ".mov", ".jpg", ".jpeg", ".png", ".zip"},
        1024 * MB,
        False,
    ),
}
# What is allowed on the PUBLIC disk (what validate() above checks and limits).
PUBLISHABLE_MAX = {"render": MAX_BYTES["image"], "glb": MAX_BYTES["glb"]}
_CONTENT_TYPE = re.compile(r"^[a-z]+/[a-z0-9][a-z0-9.+-]*$")


def validate_private_upload(kind: str, filename: str, content_type: str, size: int):
    """Check a presign request. Returns the lower-case extension."""
    if kind not in PRIVATE_KINDS:
        raise HTTPException(status_code=400, detail=f"Unknown file kind '{kind}'")
    exts, max_bytes, _ = PRIVATE_KINDS[kind]
    ext = Path(filename or "").suffix.lower()
    if ext not in exts:
        raise HTTPException(
            status_code=415,
            detail=f"{kind} files must end in {', '.join(sorted(exts))}",
        )
    if not _CONTENT_TYPE.match(content_type or ""):
        raise HTTPException(status_code=400, detail="Invalid content type")
    if size < 1 or size > max_bytes:
        raise HTTPException(
            status_code=413, detail=f"File must be 1 byte to {max_bytes // MB} MB"
        )
    return ext


def private_key(item_id: uuid.UUID, kind: str, ext: str) -> str:
    return f"items/{item_id}/{kind}/{uuid.uuid4().hex}{ext}"


def can_be_public(kind: str) -> bool:
    return kind in PRIVATE_KINDS and PRIVATE_KINDS[kind][2]
