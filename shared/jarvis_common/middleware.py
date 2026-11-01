from __future__ import annotations

import logging
import time
from typing import Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from shared.jarvis_common.correlation import (
    CORRELATION_HEADER,
    bind_correlation,
    get_correlation_id,
    reset_correlation,
    resolve_correlation_id,
)
from shared.jarvis_common.logging import get_logger, log_event

logger = get_logger("jarvis.http")


class ObservabilityMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        incoming = request.headers.get(CORRELATION_HEADER) or request.headers.get("X-Request-ID")
        correlation_token = bind_correlation(resolve_correlation_id(incoming))
        started = time.perf_counter()
        log_event(
            logger,
            logging.INFO,
            "request.start",
            extra={
                "method": request.method,
                "path": request.url.path,
            },
        )
        try:
            response = await call_next(request)
        except Exception:
            duration_ms = round((time.perf_counter() - started) * 1000, 2)
            log_event(
                logger,
                logging.ERROR,
                "request.error",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "duration_ms": duration_ms,
                },
            )
            reset_correlation(correlation_token)
            raise
        duration_ms = round((time.perf_counter() - started) * 1000, 2)
        log_event(
            logger,
            logging.INFO,
            "request.complete",
            extra={
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "duration_ms": duration_ms,
            },
        )
        response.headers[CORRELATION_HEADER] = get_correlation_id() or ""
        reset_correlation(correlation_token)
        return response


def install_observability(app, service_name: str) -> None:
    from shared.jarvis_common.logging import configure_logging

    configure_logging(service_name)
    app.add_middleware(ObservabilityMiddleware)
