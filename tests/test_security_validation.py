from __future__ import annotations

import runpy
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from scripts import jarvis_security_check
from services.alexa_ingress.app import app as alexa_app
from shared.jarvis_common.config import Settings
from shared.jarvis_common.ingress_guard import RateLimiter, reset_rate_limiter
from shared.jarvis_common.security.validation import (
    failed_checks,
    format_report,
    run_security_validation,
    validate_alexa_verify_default,
    validate_compose_exposure,
    validate_log_redaction,
    validate_sensitive_confirmation,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
COMPOSE = REPO_ROOT / "docker-compose.yml"


def test_compose_exposure_checks() -> None:
    compose_text = COMPOSE.read_text(encoding="utf-8")
    checks = {check.check_id: check for check in validate_compose_exposure(compose_text)}
    assert checks["public.alexa_only"].passed is True
    assert checks["network.web_internal"].passed is True
    assert checks["network.core_unpublished"].passed is True


def test_alexa_verify_and_policy_checks() -> None:
    compose_text = COMPOSE.read_text(encoding="utf-8")
    assert validate_alexa_verify_default(compose_text).passed is True
    assert validate_sensitive_confirmation().passed is True
    assert validate_log_redaction().passed is True


def test_run_security_validation_passes() -> None:
    checks = run_security_validation(compose_path=COMPOSE, repo_root=REPO_ROOT)
    assert not failed_checks(checks)
    report = format_report(checks)
    assert "PASS" in report
    assert "FAIL" not in report


def test_run_security_validation_detects_bad_compose(tmp_path: Path) -> None:
    bad_compose = tmp_path / "docker-compose.yml"
    bad_compose.write_text(
        COMPOSE.read_text(encoding="utf-8").replace("JARVIS_ALEXA_SKIP_VERIFY:-0", "JARVIS_ALEXA_SKIP_VERIFY:-1"),
        encoding="utf-8",
    )
    checks = run_security_validation(compose_path=bad_compose, repo_root=REPO_ROOT)
    assert any(check.check_id == "alexa.verify_default" and not check.passed for check in checks)


def test_rate_limiter() -> None:
    limiter = RateLimiter(2, window_seconds=60)
    assert limiter.allow("client-a") is True
    assert limiter.allow("client-a") is True
    assert limiter.allow("client-a") is False
    assert limiter.allow("client-b") is True
    limiter.clear()
    assert limiter.allow("client-a") is True


def test_ingress_rate_limit_and_body_size(monkeypatch: pytest.MonkeyPatch) -> None:
    from shared.jarvis_common import config as config_module
    from shared.jarvis_common import ingress_guard as ingress_guard_module

    reset_rate_limiter()
    monkeypatch.setenv("JARVIS_ALEXA_SKIP_VERIFY", "1")
    monkeypatch.setenv("JARVIS_ALEXA_RATE_LIMIT_PER_MINUTE", "2")
    monkeypatch.setenv("JARVIS_ALEXA_MAX_BODY_BYTES", "512")
    config_module.settings = Settings.from_env()
    ingress_guard_module.config_module.settings = config_module.settings

    client = TestClient(alexa_app)
    payload = {
        "request": {"type": "IntentRequest", "requestId": "rl-1", "intent": {"name": "WhatTodayIntent"}},
        "session": {"user": {"userId": "u1"}},
        "context": {"System": {"device": {"deviceId": "d1"}}},
    }

    assert client.get("/health").status_code == 200
    assert client.post("/alexa/webhook", json=payload).status_code == 200
    assert client.post("/alexa/webhook", json=payload).status_code == 200
    assert client.post("/alexa/webhook", json=payload).status_code == 429

    too_large = b"x" * 513
    assert client.post(
        "/alexa/webhook",
        content=too_large,
        headers={"content-type": "application/json", "content-length": "513"},
    ).status_code == 413


def test_client_key_and_invalid_content_length(monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    from starlette.requests import Request

    from services.alexa_ingress import app as alexa_module
    from services.alexa_ingress.app import webhook
    from shared.jarvis_common import config as config_module
    from shared.jarvis_common import ingress_guard as ingress_guard_module
    from shared.jarvis_common.ingress_guard import client_key

    scope = {
        "type": "http",
        "headers": [(b"x-forwarded-for", b"203.0.113.1, 198.51.100.2")],
        "client": ("127.0.0.1", 8000),
        "method": "POST",
        "path": "/alexa/webhook",
    }
    assert client_key(Request(scope)) == "203.0.113.1"
    assert client_key(Request({"type": "http", "headers": [], "client": ("10.0.0.5", 1)})) == "10.0.0.5"
    assert client_key(Request({"type": "http", "headers": []})) == "unknown"

    reset_rate_limiter()
    monkeypatch.setenv("JARVIS_ALEXA_SKIP_VERIFY", "1")
    monkeypatch.setenv("JARVIS_ALEXA_MAX_BODY_BYTES", "512")
    config_module.settings = Settings.from_env()
    ingress_guard_module.config_module.settings = config_module.settings

    client = TestClient(alexa_app)
    assert (
        client.post(
            "/alexa/webhook",
            content=b"{}",
            headers={"content-type": "application/json", "content-length": "not-a-number"},
        ).status_code
        == 413
    )

    async def _large_body_without_length() -> None:
        body = b"x" * 513

        async def receive():
            return {"type": "http.request", "body": body, "more_body": False}

        request = Request({"type": "http", "headers": [], "method": "POST", "path": "/alexa/webhook"}, receive)
        with pytest.raises(HTTPException) as exc:
            await webhook(request)
        assert exc.value.status_code == 413

    asyncio.run(_large_body_without_length())


def test_env_int_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JARVIS_ALEXA_RATE_LIMIT_PER_MINUTE", "not-int")
    monkeypatch.setenv("JARVIS_ALEXA_MAX_BODY_BYTES", "also-bad")
    settings = Settings.from_env()
    assert settings.alexa_rate_limit_per_minute == 60
    assert settings.alexa_max_body_bytes == 262_144


def test_security_check_script() -> None:
    assert jarvis_security_check.main(["--repo-root", str(REPO_ROOT), "--compose-file", "docker-compose.yml"]) == 0

    with patch("scripts.jarvis_security_check.run_security_validation", return_value=[]):
        with patch("scripts.jarvis_security_check.failed_checks", return_value=[object()]):
            assert jarvis_security_check.main([]) == 1

    with patch("scripts.jarvis_security_check.main", return_value=0):
        with pytest.raises(SystemExit):
            runpy.run_path("scripts/jarvis_security_check.py", run_name="__main__")
