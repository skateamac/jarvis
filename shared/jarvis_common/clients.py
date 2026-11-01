from __future__ import annotations

import os
from typing import Any, Dict

import httpx2 as httpx

from shared.jarvis_common.config import settings
from shared.jarvis_common.correlation import CORRELATION_HEADER, get_correlation_id
from shared.jarvis_common.models import CommandEnvelope, CommandResponse


def _correlation_headers() -> Dict[str, str]:
    correlation_id = get_correlation_id()
    if not correlation_id:
        return {}
    return {CORRELATION_HEADER: correlation_id}


class LocalConnectorClient:
    def calendar_read(self, envelope: CommandEnvelope) -> dict[str, object]:
        date = envelope.intent.parameters.get("date", "today")
        return {"date": date, "events": [], "source": "local_connector"}

    def calendar_write(self, envelope: CommandEnvelope) -> dict[str, object]:
        return {
            "event_id": "local-event",
            "status": "created",
            "title": envelope.intent.parameters.get("title", "Untitled event"),
        }

    def task_create(self, envelope: CommandEnvelope) -> dict[str, object]:
        return {
            "task_id": "local-task",
            "status": "created",
            "title": envelope.intent.parameters.get("title", "Untitled task"),
        }

    def oauth_start(self, account_key: str = "household") -> dict[str, object]:
        return {
            "auth_url": f"https://example.invalid/oauth?account={account_key}",
            "state": "local-state",
            "account_key": account_key,
        }

    def oauth_callback(self, code: str, state: str) -> dict[str, object]:
        return {"status": "connected", "account_key": "household", "code": code, "state": state}

    def oauth_status(self, account_key: str = "household") -> dict[str, object]:
        return {"connected": False, "account_key": account_key}


class LocalSchedulerClient:
    def schedule_job(self, envelope: CommandEnvelope) -> dict[str, object]:
        return {
            "job_id": "local-job",
            "status": "scheduled",
            "seconds": envelope.intent.parameters.get("seconds", 60),
        }


def connector_client() -> LocalConnectorClient | HttpConnectorClient:
    if os.getenv("JARVIS_USE_LOCAL_CLIENTS", "").lower() in {"1", "true", "yes"}:
        return LocalConnectorClient()
    return HttpConnectorClient()


def scheduler_client() -> LocalSchedulerClient | HttpSchedulerClient:
    if os.getenv("JARVIS_USE_LOCAL_CLIENTS", "").lower() in {"1", "true", "yes"}:
        return LocalSchedulerClient()
    return HttpSchedulerClient()


class HttpConnectorClient:
    def __init__(self, base_url: str | None = None) -> None:
        self._base_url = (base_url or settings.connectors_url).rstrip("/")

    def calendar_read(self, envelope: CommandEnvelope) -> Dict[str, Any]:
        return self._post("/internal/connectors/google/calendar/read", envelope)

    def calendar_write(self, envelope: CommandEnvelope) -> Dict[str, Any]:
        return self._post("/internal/connectors/google/calendar/write", envelope)

    def task_create(self, envelope: CommandEnvelope) -> Dict[str, Any]:
        return self._post("/internal/connectors/google/tasks/create", envelope)

    def oauth_start(self, account_key: str = "household") -> Dict[str, Any]:
        return self._get("/internal/connectors/google/oauth/start", params={"account_key": account_key})

    def oauth_callback(self, code: str, state: str) -> Dict[str, Any]:
        return self._get(
            "/internal/connectors/google/oauth/callback",
            params={"code": code, "state": state},
        )

    def oauth_status(self, account_key: str = "household") -> Dict[str, Any]:
        return self._get("/internal/connectors/google/oauth/status", params={"account_key": account_key})

    def _get(self, path: str, *, params: Dict[str, Any] | None = None) -> Dict[str, Any]:
        with httpx.Client(timeout=15.0) as client:
            response = client.get(
                f"{self._base_url}{path}",
                params=params,
                headers=_correlation_headers(),
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError("Connector response must be a JSON object.")
            return payload

    def _post(self, path: str, envelope: CommandEnvelope) -> Dict[str, Any]:
        with httpx.Client(timeout=10.0) as client:
            response = client.post(
                f"{self._base_url}{path}",
                json=envelope.model_dump(),
                headers=_correlation_headers(),
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError("Connector response must be a JSON object.")
            return payload


class HttpSchedulerClient:
    def __init__(self, base_url: str | None = None) -> None:
        self._base_url = (base_url or settings.scheduler_url).rstrip("/")

    def schedule_job(self, envelope: CommandEnvelope) -> Dict[str, Any]:
        with httpx.Client(timeout=10.0) as client:
            response = client.post(
                f"{self._base_url}/internal/scheduler/jobs",
                json=envelope.model_dump(),
                headers=_correlation_headers(),
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError("Scheduler response must be a JSON object.")
            return payload


class HttpCoreClient:
    def __init__(self, base_url: str | None = None) -> None:
        self._base_url = (base_url or settings.core_url).rstrip("/")

    def send_command(self, envelope: CommandEnvelope) -> CommandResponse:
        with httpx.Client(timeout=10.0) as client:
            response = client.post(
                f"{self._base_url}/v1/commands",
                json=envelope.model_dump(),
                headers=_correlation_headers(),
            )
            response.raise_for_status()
            return CommandResponse.model_validate(response.json())
