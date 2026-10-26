"""Shared fake object store (loaded as a pytest plugin from conftest)."""

import pytest

from app.core import ratelimit
from app.core.config import settings
from app.main import app
from app.services.object_store import get_store, get_store_or_none


class FakeStore:
    """In-memory stand-in for the S3-compatible store (same methods)."""

    def __init__(self):
        self.objects = {}
        self.signed = []

    def presign_put(self, key, content_type, size, expires=600):
        self.signed.append((key, content_type, size))
        return {
            "url": f"https://store.invalid/bucket/{key}?sig=1",
            "method": "PUT",
            "headers": {"Content-Type": content_type},
        }

    def presign_get(self, key, expires=900, filename=None):
        return f"https://store.invalid/bucket/{key}?sig=1"

    def head(self, key):
        return len(self.objects[key]) if key in self.objects else None

    def get_bytes(self, key, max_bytes):
        return self.objects[key][: max_bytes + 1]

    def delete(self, key):
        self.objects.pop(key, None)


@pytest.fixture
def store(tmp_path, monkeypatch):
    ratelimit.reset()
    monkeypatch.setattr(settings, "MEDIA_DIR", str(tmp_path / "media"))
    monkeypatch.setattr(settings, "PUBLIC_RATE_LIMIT", 100)
    fake = FakeStore()
    app.dependency_overrides[get_store] = lambda: fake
    app.dependency_overrides[get_store_or_none] = lambda: fake
    yield fake
    app.dependency_overrides.pop(get_store, None)
    app.dependency_overrides.pop(get_store_or_none, None)
    ratelimit.reset()
