from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime
from typing import Any, Dict, Iterable, Mapping, Optional

from shared.jarvis_common.correlation import get_correlation_id, get_service_name

SENSITIVE_KEY_PATTERN = re.compile(
    r"(password|secret|token|pin|authorization|api[_-]?key|refresh|access|credential)",
    re.IGNORECASE,
)
REDACTED = "[REDACTED]"


def redact_value(key: str, value: Any) -> Any:
    if SENSITIVE_KEY_PATTERN.search(key):
        return REDACTED
    if isinstance(value, Mapping):
        return redact_mapping(value)
    if isinstance(value, list):
        return [redact_value(key, item) for item in value]
    return value


def redact_mapping(payload: Mapping[str, Any]) -> Dict[str, Any]:
    return {key: redact_value(key, value) for key, value in payload.items()}


class JsonLogFormatter(logging.Formatter):
    _BUILTIN_KEYS = frozenset(logging.LogRecord("", 0, "", 0, "", (), None).__dict__)

    def format(self, record: logging.LogRecord) -> str:
        payload: Dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        service = get_service_name()
        if service:
            payload["service"] = service
        correlation_id = get_correlation_id()
        if correlation_id:
            payload["correlation_id"] = correlation_id
        if isinstance(record.args, dict):
            payload.update(redact_mapping(record.args))
        for key, value in record.__dict__.items():
            if key in self._BUILTIN_KEYS or key in payload:
                continue
            payload[key] = redact_value(key, value)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(service_name: str, *, level: int = logging.INFO) -> None:
    from shared.jarvis_common.correlation import bind_service

    bind_service(service_name)
    root = logging.getLogger()
    root.handlers.clear()
    handler = logging.StreamHandler()
    handler.setFormatter(JsonLogFormatter())
    root.addHandler(handler)
    root.setLevel(level)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def log_event(
    logger: logging.Logger,
    level: int,
    event: str,
    *,
    extra: Optional[Mapping[str, Any]] = None,
) -> None:
    fields = redact_mapping(extra or {})
    record = logger.makeRecord(
        logger.name,
        level,
        "(unknown)",
        0,
        event,
        (),
        None,
    )
    setattr(record, "event", event)
    for key, value in fields.items():
        setattr(record, key, value)
    logger.handle(record)


def redact_env_lines(lines: Iterable[str]) -> list[str]:
    redacted: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            redacted.append(line.rstrip("\n"))
            continue
        key, value = stripped.split("=", 1)
        if SENSITIVE_KEY_PATTERN.search(key):
            redacted.append(f"{key}={REDACTED}")
        else:
            redacted.append(f"{key}={value}")
    return redacted
