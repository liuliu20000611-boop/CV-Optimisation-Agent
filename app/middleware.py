"""Request ID injection and lightweight rate limiting (no PII in logs)."""

from __future__ import annotations

import logging
import time
import uuid
from collections import defaultdict

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

logger = logging.getLogger("resume_agent.http")


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Assign X-Request-ID; logs only method/path/status/id — never body or secrets."""

    async def dispatch(self, request: Request, call_next) -> Response:
        rid = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        request.state.request_id = rid
        response = await call_next(request)
        response.headers["X-Request-ID"] = rid
        log_request_summary(request, response)
        return response


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Sliding window per client IP for /api/* only. Reads settings per request (supports tests)."""

    def __init__(self, app) -> None:
        super().__init__(app)
        self._hits: dict[str, list[float]] = defaultdict(list)

    def _should_limit(self, path: str) -> bool:
        return path.startswith("/api/")

    async def dispatch(self, request: Request, call_next) -> Response:
        from app.config import get_settings

        settings = get_settings()
        if not settings.rate_limit_enabled or settings.testing or not self._should_limit(request.url.path):
            return await call_next(request)

        client = request.client.host if request.client else "unknown"
        now = time.time()
        window = self._hits[client]
        cutoff = now - 60.0
        window[:] = [t for t in window if t > cutoff]
        if len(window) >= settings.rate_limit_per_minute:
            rid = getattr(request.state, "request_id", "-")
            logger.warning("rate_limited request_id=%s ip=%s path=%s", rid, client, request.url.path)
            return JSONResponse(
                {"detail": "请求过于频繁，请稍后再试"},
                status_code=429,
                headers={"Retry-After": "60"},
            )
        window.append(now)
        return await call_next(request)


def log_request_summary(request: Request, response: Response) -> None:
    """Safe one-line summary for access-style logging."""
    rid = getattr(request.state, "request_id", "-")
    logger.info(
        "request_id=%s method=%s path=%s status=%s",
        rid,
        request.method,
        request.url.path,
        response.status_code,
    )
