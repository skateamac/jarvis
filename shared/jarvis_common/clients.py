from __future__ import annotations

import os
from typing import Any, Dict

import httpx2 as httpx

from shared.jarvis_common.config import settings
from shared.jarvis_common.models import CommandEnvelope, CommandResponse


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

    def _post(self, path: str, envelope: CommandEnvelope) -> Dict[str, Any]:
        with httpx.Client(timeout=10.0) as client:
            response = client.post(
                f"{self._base_url}{path}",
                json=envelope.model_dump(),
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
            )
            response.raise_for_status()
            return CommandResponse.model_validate(response.json())
