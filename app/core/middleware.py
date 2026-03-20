import time
import logging
from starlette.middleware.base import BaseHTTPMiddleware
from fastapi import Request

logger = logging.getLogger(__name__)


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

        # Optional: Inject server timing into headers
        response.headers["X-Process-Time"] = str(process_time)
        return response
