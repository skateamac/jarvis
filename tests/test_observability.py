from __future__ import annotations

import json
import logging
from io import StringIO
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from shared.jarvis_common.correlation import (
    CORRELATION_HEADER,
    bind_correlation,
    get_correlation_id,
    get_service_name,
    new_correlation_id,
    reset_correlation,
    resolve_correlation_id,
)
from shared.jarvis_common.logging import (
    JsonLogFormatter,
    configure_logging,
    get_logger,
    log_event,
    redact_env_lines,
    redact_mapping,
    redact_value,
)
from shared.jarvis_common.middleware import ObservabilityMiddleware, install_observability


def test_correlation_helpers() -> None:
    assert resolve_correlation_id("  req-1  ") == "req-1"
    generated = resolve_correlation_id(None)
    assert len(generated) == 36

    token = bind_correlation("abc")
    assert get_correlation_id() == "abc"
    reset_correlation(token)
    assert get_correlation_id() is None
    assert len(new_correlation_id()) == 36

    from shared.jarvis_common.correlation import bind_service, reset_service

    service_token = bind_service("svc")
    assert get_service_name() == "svc"
    reset_service(service_token)


def test_redaction() -> None:
    payload = {
        "title": "Dinner",
        "access_token": "secret-value",
        "nested": {"refresh_token": "x", "ok": True},
        "items": [{"pin": "1234"}],
    }
    redacted = redact_mapping(payload)
    assert redacted["access_token"] == "[REDACTED]"
    assert redacted["nested"]["refresh_token"] == "[REDACTED]"
    assert redacted["nested"]["ok"] is True
    assert redacted["items"][0]["pin"] == "[REDACTED]"
    assert redact_value("username", "scott") == "scott"

    lines = redact_env_lines(
        [
            "# comment",
            "",
            "POSTGRES_PASSWORD=local",
            "GOOGLE_CLIENT_SECRET=abc",
            "JARVIS_HOUSEHOLD_PIN=1234",
        ]
    )
    assert lines[0] == "# comment"
    assert lines[2] == "POSTGRES_PASSWORD=[REDACTED]"
    assert lines[3] == "GOOGLE_CLIENT_SECRET=[REDACTED]"


def test_json_log_formatter_and_configure() -> None:
    configure_logging("jarvis-test", level=logging.DEBUG)
    assert get_service_name() == "jarvis-test"

    formatter = JsonLogFormatter()
    logger = get_logger("jarvis.test")
    stream = StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(formatter)
    logger.handlers = [handler]
    logger.setLevel(logging.INFO)

    token = bind_correlation("corr-123")
    log_event(logger, logging.INFO, "test.event", extra={"request_id": "corr-123", "token": "hidden"})
    reset_correlation(token)

    payload = json.loads(stream.getvalue().strip())
    assert payload["service"] == "jarvis-test"
    assert payload["correlation_id"] == "corr-123"
    assert payload["event"] == "test.event"
    assert payload["token"] == "[REDACTED]"

    record = logger.makeRecord(
        logger.name, logging.INFO, __file__, 10, "value=%(token)s", ({"token": "hidden"},), None
    )
    formatted_args = json.loads(formatter.format(record))
    assert formatted_args["token"] == "[REDACTED]"

    record = logger.makeRecord(logger.name, logging.ERROR, __file__, 10, "boom", {}, None)
    record.exc_info = (TypeError, TypeError("bad"), None)
    formatted_exc = formatter.format(record)
    assert "exception" in formatted_exc


def test_observability_middleware() -> None:
    app = FastAPI()
    install_observability(app, "jarvis-middleware-test")

    @app.get("/ok")
    def ok() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/fail")
    def fail() -> None:
        raise RuntimeError("boom")

    client = TestClient(app)
    response = client.get("/ok", headers={CORRELATION_HEADER: "trace-1"})
    assert response.status_code == 200
    assert response.headers[CORRELATION_HEADER] == "trace-1"

    generated = client.get("/ok")
    assert generated.headers[CORRELATION_HEADER]

    with pytest.raises(RuntimeError, match="boom"):
        client.get("/fail")


def test_middleware_direct_dispatch() -> None:
    app = FastAPI()
    app.add_middleware(ObservabilityMiddleware)

    @app.get("/ping")
    def ping() -> dict[str, str]:
        return {"pong": "1"}

    client = TestClient(app)
    assert client.get("/ping", headers={"X-Request-ID": "req-alt"}).json()["pong"] == "1"


def test_empty_correlation_headers() -> None:
    from shared.jarvis_common.clients import _correlation_headers
    from shared.jarvis_common.correlation import bind_correlation, get_correlation_id, reset_correlation

    existing = get_correlation_id()
    if existing is not None:
        reset_correlation(bind_correlation(existing))
    assert _correlation_headers() == {}
