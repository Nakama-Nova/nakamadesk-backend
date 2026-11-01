"""
A tiny in-process, per-IP rate limiter for the public (no-login) endpoints.

ponytail: in memory and per process. Fine for one API process behind one
proxy; with several workers each has its own counts, so use a shared store
(Redis) or the proxy's own limiter if that ever changes. Behind a reverse
proxy run uvicorn with --proxy-headers so request.client is the real caller.
"""

import os
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request

from app.core.config import settings

_hits: dict = defaultdict(deque)


def client_ip(request: Request) -> str:
    """
    The caller's address, used by every limiter. Forwarding headers are client-supplied
    unless a trusted proxy sets them, so they are believed only on Vercel (it sets
    x-vercel-forwarded-for itself) or when TRUST_PROXY_HEADERS is set because a proxy you
    run overwrites X-Forwarded-For. Anywhere else the socket address is used.
    """
    if os.getenv("VERCEL") or os.getenv("TRUST_PROXY_HEADERS"):
        forwarded = request.headers.get(
            "x-vercel-forwarded-for"
        ) or request.headers.get("x-forwarded-for", "")
        first = forwarded.split(",")[0].strip()
        if first:
            return first
    return request.client.host if request.client else "unknown"


def reset() -> None:
    """Forget all counts (used by tests)."""
    _hits.clear()


def public_rate_limit(scope: str):
    """FastAPI dependency: at most settings.PUBLIC_RATE_LIMIT calls/minute per IP."""

    def dep(request: Request) -> None:
        key = (scope, client_ip(request))
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
