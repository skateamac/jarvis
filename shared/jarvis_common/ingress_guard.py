from __future__ import annotations

import logging
import time
from collections import defaultdict
from typing import Callable, DefaultDict, List

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from shared.jarvis_common import config as config_module
from shared.jarvis_common.logging import get_logger, log_event

logger = get_logger("jarvis.ingress")


class RateLimiter:
    def __init__(self, limit: int, *, window_seconds: int = 60) -> None:
        self._limit = max(limit, 1)
        self._window = window_seconds
        self._hits: DefaultDict[str, List[float]] = defaultdict(list)

    def clear(self) -> None:
        self._hits.clear()

    def allow(self, key: str) -> bool:
        now = time.time()
        recent = [stamp for stamp in self._hits[key] if now - stamp < self._window]
        if len(recent) >= self._limit:
            self._hits[key] = recent
            return False
        recent.append(now)
        self._hits[key] = recent
        return True


_rate_limiter: RateLimiter | None = None


def get_rate_limiter() -> RateLimiter:
    global _rate_limiter
    if _rate_limiter is None:
        _rate_limiter = RateLimiter(config_module.settings.alexa_rate_limit_per_minute)
    return _rate_limiter


def reset_rate_limiter() -> None:
    global _rate_limiter
    _rate_limiter = None


def client_key(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client is not None:
        return request.client.host
    return "unknown"


class IngressGuardMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        if request.url.path == "/health":
            return await call_next(request)

        content_length = request.headers.get("content-length")
        if content_length is not None:
            try:
                size = int(content_length)
            except ValueError:
                size = config_module.settings.alexa_max_body_bytes + 1
            if size > config_module.settings.alexa_max_body_bytes:
                log_event(
                    logger,
                    logging.WARNING,
                    "ingress.body_rejected",
                    extra={"path": request.url.path, "content_length": size},
                )
                return JSONResponse(
                    status_code=413,
                    content={"detail": "Request body too large."},
                )

        if request.method == "POST" and request.url.path == "/alexa/webhook":
            if not get_rate_limiter().allow(client_key(request)):
                log_event(
                    logger,
                    logging.WARNING,
                    "ingress.rate_limited",
                    extra={"path": request.url.path, "client": client_key(request)},
                )
                return JSONResponse(
                    status_code=429,
                    content={"detail": "Rate limit exceeded."},
                )

        return await call_next(request)


def install_ingress_guard(app) -> None:
    app.add_middleware(IngressGuardMiddleware)
