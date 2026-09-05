from __future__ import annotations

import uuid
from contextvars import ContextVar, Token
from typing import Optional

CORRELATION_HEADER = "x-correlation-id"

_correlation_id: ContextVar[Optional[str]] = ContextVar("correlation_id", default=None)
_service_name: ContextVar[Optional[str]] = ContextVar("service_name", default=None)


def get_correlation_id() -> Optional[str]:
    return _correlation_id.get()


def get_service_name() -> Optional[str]:
    return _service_name.get()


def bind_correlation(correlation_id: str) -> Token:
    return _correlation_id.set(correlation_id)


def bind_service(service_name: str) -> Token:
    return _service_name.set(service_name)


def reset_correlation(token: Token) -> None:
    _correlation_id.reset(token)


def reset_service(token: Token) -> None:
    _service_name.reset(token)


def new_correlation_id() -> str:
    return str(uuid.uuid4())


def resolve_correlation_id(header_value: Optional[str]) -> str:
    if header_value and header_value.strip():
        return header_value.strip()
    return new_correlation_id()
