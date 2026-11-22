import logging
import re
import time

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.config import settings

logger = logging.getLogger(__name__)


class HideSecretInAccessLog(logging.Filter):
    """uvicorn's access log prints the whole URL; keep `?secret=...` (Brevo's webhook
    secret) out of it."""

    _SECRET = re.compile(r"([?&]secret=)[^&\s]*")

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            record.args = tuple(
                (
                    self._SECRET.sub(lambda m: m.group(1) + "***", a)
                    if isinstance(a, str)
                    else a
                )
                for a in record.args
            )
        return True


class TimingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        start_time = time.time()

        response = await call_next(request)

        process_time = time.time() - start_time
        process_time_ms = process_time * 1000

        # Log queries exceeding 300ms thresholds (0.3 seconds)
        if process_time_ms > 300:
            logger.warning(
                f"SLOW QUERY DETECTED: {request.method} {request.url.path} "
                f"took {process_time_ms:.2f}ms"
            )

        if (
            not settings.IS_PROD
        ):  # timing is a side channel; only show it while developing
            response.headers["X-Process-Time"] = str(process_time)
        return response


SECURITY_HEADERS = {
    "Strict-Transport-Security": "max-age=63072000; includeSubDomains",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Browser hardening on every response. A route that sets its own value (the Lab's
    CSP and Referrer-Policy, /media's nosniff) keeps it."""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        for name, value in SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        return response
