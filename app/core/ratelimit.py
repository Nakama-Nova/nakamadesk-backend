"""
A tiny in-process, per-IP rate limiter for the public (no-login) endpoints.

ponytail: in memory and per process. Fine for one API process behind one
proxy; with several workers each has its own counts, so use a shared store
(Redis) or the proxy's own limiter if that ever changes. Behind a reverse
proxy run uvicorn with --proxy-headers so request.client is the real caller.
"""

import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request

from app.core.config import settings

_hits: dict = defaultdict(deque)


def reset() -> None:
    """Forget all counts (used by tests)."""
    _hits.clear()


def public_rate_limit(scope: str):
    """FastAPI dependency: at most settings.PUBLIC_RATE_LIMIT calls/minute per IP."""

    def dep(request: Request) -> None:
        key = (scope, request.client.host if request.client else "unknown")
        now = time.monotonic()
        q = _hits[key]
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= settings.PUBLIC_RATE_LIMIT:
            raise HTTPException(
                status_code=429,
                detail="Too many requests. Please try again in a minute.",
            )
        q.append(now)

    return dep
