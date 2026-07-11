from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import patch

import httpx2 as httpx
import pytest
from fastapi.testclient import TestClient

from services.alexa_ingress.app import app as alexa_app
from services.connectors_google.app import app as connectors_app
from services.core.app import app as core_app
from services.policy.app import app as policy_app
from services.scheduler.app import app as scheduler_app
from services.web.app import app as web_app
from shared.jarvis_common.alexa import (
    alexa_error_response,
    alexa_response_for,
    parse_alexa_envelope,
    verify_alexa_request,
)
from shared.jarvis_common.clients import (
    HttpConnectorClient,
    HttpCoreClient,
    HttpSchedulerClient,
    LocalConnectorClient,
    LocalSchedulerClient,
    connector_client,
    scheduler_client,
)
from shared.jarvis_common.config import Settings, settings
from shared.jarvis_common.models import (
    Actor,
    CalendarEventCreateRequest,
    CommandEnvelope,
    CommandResponse,
    Context,
    Intent,
    PolicyEvaluationRequest,
    SchedulerJobRequest,
    TaskCreateRequest,
)
from shared.jarvis_common.orchestrator import CommandOrchestrator, intent_action_type, policy_request_for
from shared.jarvis_common.policy_engine import evaluate_policy
from shared.jarvis_common.stores import ApprovalStore, AuditStore

client_core = TestClient(core_app)
client_alexa = TestClient(alexa_app)
client_policy = TestClient(policy_app)
client_scheduler = TestClient(scheduler_app)
client_connectors = TestClient(connectors_app)
client_web = TestClient(web_app)


def _envelope(
    intent_name: str = "what_today",
    *,
    role: str = "adult",
    source: str = "dashboard",
    parameters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "request_id": "req-test",
        "source": source,
        "actor": {"user_id": "u1", "role": role, "device_id": "d1"},
        "intent": {"name": intent_name, "parameters": parameters or {}},
        "context": {"timezone": "America/Chicago", "locale": "en-US"},
    }


def test_core_health() -> None:
    assert client_core.get("/health").json() == {"status": "ok"}


def test_core_command_what_today_executes() -> None:
    response = client_core.post("/v1/commands", json=_envelope("what_today"))
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "executed"
    assert payload["result"]["source"] == "local_connector"


def test_core_command_add_event_requires_confirmation() -> None:
    response = client_core.post(
        "/v1/commands",
        json=_envelope("add_event", parameters={"title": "Dinner", "calendar_scope": "family_shared"}),
    )
    payload = response.json()
    assert payload["status"] == "requires_confirmation"
    assert payload["approval_id"]


def test_core_command_child_denied() -> None:
    response = client_core.post("/v1/commands", json=_envelope("add_task", role="child", source="dashboard"))
    assert response.json()["status"] == "denied"


def test_core_execute_approval_flow() -> None:
    create = client_core.post(
        "/v1/commands",
        json=_envelope("add_event", parameters={"title": "Dinner", "calendar_scope": "family_shared"}),
    )
    approval_id = create.json()["approval_id"]
    client_web.post(
        f"/v1/approvals/{approval_id}/confirm",
        json={"confirmation_type": "app", "confirmation_value": "ok"},
    )
    execute = client_core.post(f"/internal/approvals/{approval_id}/execute")
    assert execute.json()["status"] == "executed"


def test_core_execute_missing_approval() -> None:
    response = client_core.post("/internal/approvals/missing/execute")
    assert response.json()["status"] == "failed"


def test_policy_health_and_rules() -> None:
    assert client_policy.get("/health").json() == {"status": "ok"}

    allow = client_policy.post(
        "/internal/policy/evaluate",
        json={
            "actor": {"user_id": "u1", "role": "adult", "device_id": "d1"},
            "action": {"type": "calendar_read", "resource": "family_shared"},
            "context": {"source": "dashboard"},
        },
    )
    assert allow.json()["decision"] == "ALLOW"

    deny = client_policy.post(
        "/internal/policy/evaluate",
        json={
            "actor": {"user_id": "u1", "role": "adult", "device_id": "d1"},
            "action": {"type": "calendar_write", "resource": "work_calendar"},
            "context": {},
        },
    )
    assert deny.json()["decision"] == "DENY"


def test_alexa_webhook_intent_request(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "services.alexa_ingress.app.core_client.send_command",
        lambda envelope: type(
            "Resp",
            (),
            {
                "status": "executed",
                "message": "Here is your schedule.",
            },
        )(),
    )
    response = client_alexa.post(
        "/alexa/webhook",
        json={
            "request": {
                "type": "IntentRequest",
                "requestId": "alexa-1",
                "intent": {"name": "WhatTodayIntent", "slots": {"date": {"value": "2026-07-11"}}},
            },
            "session": {"user": {"userId": "alexa-user"}},
            "context": {"System": {"device": {"deviceId": "echo-1"}}},
        },
    )
    assert response.status_code == 200
    assert "schedule" in response.json()["response"]["outputSpeech"]["text"]


def test_alexa_webhook_invalid_request_type() -> None:
    response = client_alexa.post(
        "/alexa/webhook",
        json={"request": {"type": "LaunchRequest", "requestId": "alexa-2"}},
    )
    assert response.status_code == 400


def test_alexa_signature_required_when_not_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JARVIS_ALEXA_SKIP_VERIFY", "0")
    from shared.jarvis_common import alexa as alexa_module
    from shared.jarvis_common import config as config_module

    config_module.settings = Settings.from_env()
    alexa_module.settings = config_module.settings
    with pytest.raises(ValueError, match="Missing Alexa signature headers"):
        verify_alexa_request({}, b"{}")


def test_web_timeline_and_approvals() -> None:
    client_core.post("/v1/commands", json=_envelope("what_today"))
    client_core.post(
        "/v1/commands",
        json=_envelope("add_event", parameters={"title": "Date night", "calendar_scope": "family_shared"}),
    )

    timeline = client_web.get("/v1/timeline")
    assert timeline.status_code == 200
    assert len(timeline.json()) >= 2

    pending = client_web.get("/v1/approvals/pending")
    assert pending.status_code == 200
    assert len(pending.json()["approvals"]) == 1


def test_web_confirm_and_reject_approval() -> None:
    create = client_core.post(
        "/v1/commands",
        json=_envelope("add_event", parameters={"title": "Dinner", "calendar_scope": "family_shared"}),
    )
    approval_id = create.json()["approval_id"]

    bad_pin = client_web.post(
        f"/v1/approvals/{approval_id}/confirm",
        json={"confirmation_type": "pin", "confirmation_value": "0000"},
    )
    assert bad_pin.status_code == 403

    confirmed = client_web.post(
        f"/v1/approvals/{approval_id}/confirm",
        json={"confirmation_type": "pin", "confirmation_value": "1234"},
    )
    assert confirmed.json()["executed"] is True

    create2 = client_core.post(
        "/v1/commands",
        json=_envelope("add_event", parameters={"title": "Lunch", "calendar_scope": "family_shared"}),
    )
    approval_id2 = create2.json()["approval_id"]
    rejected = client_web.post(f"/v1/approvals/{approval_id2}/reject")
    assert rejected.json()["status"] == "rejected"


def test_web_dashboard_endpoints() -> None:
    task = client_web.post("/v1/tasks", json={"list": "shopping", "title": "Milk"})
    assert task.status_code == 200
    assert task.json()["status"] == "created"

    day = client_web.get("/v1/calendar/day", params={"date": "2026-07-11"})
    assert day.status_code == 200
    assert day.json()["date"] == "2026-07-11"

    event = client_web.post(
        "/v1/calendar/events",
        json={
            "calendar_scope": "family_shared",
            "title": "Dinner",
            "start_time": "2026-07-11T19:00:00-05:00",
            "end_time": "2026-07-11T20:00:00-05:00",
        },
    )
    assert event.status_code == 200
    assert event.json()["status"] == "requires_confirmation"


def test_connectors_and_scheduler() -> None:
    assert client_connectors.get("/health").json() == {"status": "ok"}
    assert client_scheduler.get("/health").json() == {"status": "ok"}

    read = client_connectors.post(
        "/internal/connectors/google/calendar/read",
        json=_envelope("what_today", parameters={"date": "2026-07-11"}),
    )
    assert read.json()["events"]

    write = client_connectors.post(
        "/internal/connectors/google/calendar/write",
        json=_envelope("add_event", parameters={"title": "Dinner", "calendar_scope": "family_shared"}),
    )
    assert write.json()["status"] == "created"

    work_write = client_connectors.post(
        "/internal/connectors/google/calendar/write",
        json=_envelope("add_event", parameters={"calendar_scope": "work"}),
    )
    assert work_write.status_code == 403

    task = client_connectors.post(
        "/internal/connectors/google/tasks/create",
        json=_envelope("add_task", parameters={"title": "Milk", "list": "shopping"}),
    )
    assert task.json()["status"] == "created"

    timer = client_scheduler.post("/internal/scheduler/jobs", json=_envelope("set_timer", parameters={"seconds": 30}))
    assert timer.json()["status"] == "scheduled"

    job = client_scheduler.post(
        "/internal/scheduler/jobs/create",
        json={"job_type": "date_night_check", "payload": {}, "run_at": None},
    )
    assert job.json()["status"] == "scheduled"


def test_models_and_policy_helpers() -> None:
    actor = Actor(user_id="u1", role="adult", device_id="d1")
    envelope = CommandEnvelope(
        request_id="req-1",
        source="dashboard",
        actor=actor,
        intent=Intent(name="add_task", parameters={"title": "Milk"}),
        context=Context(),
    )
    assert intent_action_type("add_task") == "task_create"
    policy_request = policy_request_for(envelope)
    assert policy_request.action["type"] == "task_create"

    work_envelope = CommandEnvelope(
        request_id="req-2",
        source="dashboard",
        actor=actor,
        intent=Intent(name="add_event", parameters={"calendar_scope": "work"}),
        context=Context(),
    )
    assert policy_request_for(work_envelope).action["resource"] == "work_calendar"

    task_request = TaskCreateRequest(list="shopping", title="Milk")
    calendar_request = CalendarEventCreateRequest(
        calendar_scope="family_shared",
        title="Dinner",
        start_time="2026-07-11T19:00:00-05:00",
        end_time="2026-07-11T20:00:00-05:00",
    )
    assert task_request.priority == "normal"
    assert calendar_request.location is None


def test_orchestrator_failure_and_unsupported_intent() -> None:
    class BrokenConnector(LocalConnectorClient):
        def calendar_read(self, envelope: CommandEnvelope) -> dict[str, object]:
            raise RuntimeError("boom")

    orchestrator = CommandOrchestrator(
        policy_evaluate=evaluate_policy,
        approval_store=ApprovalStore(),
        audit_store=AuditStore(),
        connector_client=BrokenConnector(),
    )
    envelope = CommandEnvelope(
        request_id="req-fail",
        source="dashboard",
        actor=Actor(user_id="u1", role="adult", device_id="d1"),
        intent=Intent(name="what_today", parameters={}),
        context=Context(),
    )
    failed = orchestrator.handle(envelope)
    assert failed.status == "failed"

    class BadIntentOrchestrator(CommandOrchestrator):
        def _execute(self, envelope: CommandEnvelope) -> dict[str, Any]:
            raise ValueError("Unsupported intent: bad")

    bad = BadIntentOrchestrator(
        policy_evaluate=evaluate_policy,
        approval_store=ApprovalStore(),
        audit_store=AuditStore(),
    )
    with pytest.raises(ValueError, match="Unsupported intent"):
        bad._execute(
            CommandEnvelope(
                request_id="req-bad",
                source="dashboard",
                actor=Actor(user_id="u1", role="adult", device_id="d1"),
                intent=Intent(name="what_today", parameters={}),
                context=Context(),
            )
        )


def test_alexa_helpers() -> None:
    payload = {
        "request": {
            "type": "IntentRequest",
            "requestId": "abc",
            "intent": {
                "name": "AddTaskIntent",
                "slots": {"title": {"value": "Milk"}, "list": {"value": "shopping"}},
            },
        },
        "session": {"user": {"userId": "u1"}},
        "context": {"System": {"device": {"deviceId": "d1"}}},
    }
    envelope = parse_alexa_envelope(payload)
    assert envelope.intent.name == "add_task"
    assert envelope.intent.parameters["title"] == "Milk"
    assert alexa_response_for("hello")["response"]["outputSpeech"]["text"] == "hello"
    assert alexa_error_response()["response"]["shouldEndSession"] is True

    with pytest.raises(ValueError, match="Unsupported Alexa intent"):
        parse_alexa_envelope(
            {
                "request": {"type": "IntentRequest", "requestId": "x", "intent": {"name": "UnknownIntent"}},
                "session": {},
                "context": {},
            }
        )

    stale_time = (datetime.now(UTC) - timedelta(minutes=10)).isoformat()
    from shared.jarvis_common import alexa as alexa_module

    alexa_module.settings = Settings(alexa_skip_verify=False)
    with pytest.raises(ValueError, match="Missing Alexa signature headers"):
        verify_alexa_request({"timestamp": stale_time}, b"{}")
    with pytest.raises(ValueError, match="Empty Alexa request body"):
        verify_alexa_request({"signature": "sig", "signaturecertchainurl": "url"}, b"")
    with pytest.raises(ValueError, match="Invalid Alexa timestamp"):
        verify_alexa_request(
            {"signature": "sig", "signaturecertchainurl": "url", "timestamp": "not-a-time"},
            b"{}",
        )
    fresh_time = datetime.now(UTC).isoformat()
    with pytest.raises(ValueError, match="stale"):
        verify_alexa_request(
            {
                "signature": "sig",
                "signaturecertchainurl": "url",
                "timestamp": stale_time,
            },
            b"{}",
        )
    verify_alexa_request(
        {"Signature": "sig", "SignatureCertChainUrl": "url", "Timestamp": fresh_time},
        b"{}",
    )


def test_http_clients_with_mock_transport() -> None:
    envelope = CommandEnvelope(
        request_id="req-http",
        source="dashboard",
        actor=Actor(user_id="u1", role="adult", device_id="d1"),
        intent=Intent(name="what_today", parameters={}),
        context=Context(),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/v1/commands"):
            return httpx.Response(
                200,
                json={
                    "request_id": "req-http",
                    "status": "executed",
                    "message": "ok",
                    "result": {},
                },
            )
        return httpx.Response(200, json={"ok": True, "path": request.url.path})

    transport = httpx.MockTransport(handler)

    class ClientFactory:
        def __init__(self, shared_client: httpx.Client) -> None:
            self._shared_client = shared_client

        def __call__(self, *args: object, **kwargs: object) -> "ClientFactory":
            return self

        def __enter__(self) -> httpx.Client:
            return self._shared_client

        def __exit__(self, *args: object) -> bool:
            return False

    with httpx.Client(transport=transport, base_url="http://test") as client:
        with patch(
            "shared.jarvis_common.clients.httpx.Client",
            ClientFactory(client),
        ):
            connector = HttpConnectorClient(base_url="http://test")
            assert connector.calendar_read(envelope)["ok"] is True
            assert connector.calendar_write(envelope)["path"].endswith("/calendar/write")
            assert connector.task_create(envelope)["ok"] is True
            scheduler = HttpSchedulerClient(base_url="http://test")
            assert scheduler.schedule_job(envelope)["path"] == "/internal/scheduler/jobs"
            core = HttpCoreClient(base_url="http://test")
            assert core.send_command(envelope).status == "executed"

    def bad_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=["not", "a", "dict"])

    bad_transport = httpx.MockTransport(bad_handler)
    with httpx.Client(transport=bad_transport, base_url="http://test") as bad_client:
        with patch(
            "shared.jarvis_common.clients.httpx.Client",
            ClientFactory(bad_client),
        ):
            connector = HttpConnectorClient(base_url="http://test")
            with pytest.raises(ValueError, match="JSON object"):
                connector.task_create(envelope)
            scheduler = HttpSchedulerClient(base_url="http://test")
            with pytest.raises(ValueError, match="JSON object"):
                scheduler.schedule_job(envelope)


def test_client_factories_and_settings() -> None:
    with patch.dict(os.environ, {"JARVIS_USE_LOCAL_CLIENTS": "1"}, clear=False):
        assert isinstance(connector_client(), LocalConnectorClient)
        assert isinstance(scheduler_client(), LocalSchedulerClient)

    with patch.dict(os.environ, {"JARVIS_USE_LOCAL_CLIENTS": "0", "JARVIS_POLICY_VERSION": "v9"}, clear=False):
        loaded = Settings.from_env()
        assert loaded.policy_version == "v9"
        assert isinstance(connector_client(), HttpConnectorClient)


def test_web_not_found_and_non_pending_approval() -> None:
    missing = client_web.post(
        "/v1/approvals/does-not-exist/confirm",
        json={"confirmation_type": "app", "confirmation_value": "ok"},
    )
    assert missing.status_code == 404

    create = client_core.post(
        "/v1/commands",
        json=_envelope("add_event", parameters={"title": "Brunch", "calendar_scope": "family_shared"}),
    )
    approval_id = create.json()["approval_id"]
    client_web.post(
        f"/v1/approvals/{approval_id}/confirm",
        json={"confirmation_type": "app", "confirmation_value": "ok"},
    )
    again = client_web.post(
        f"/v1/approvals/{approval_id}/confirm",
        json={"confirmation_type": "app", "confirmation_value": "ok"},
    )
    assert again.json()["executed"] is False


def test_alexa_webhook_response_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    from services.alexa_ingress import app as alexa_module

    def make_response(status: str, message: str):
        return type("Resp", (), {"status": status, "message": message})()

    monkeypatch.setattr(
        alexa_module.core_client,
        "send_command",
        lambda envelope: make_response("requires_confirmation", "Please confirm."),
    )
    confirm = client_alexa.post(
        "/alexa/webhook",
        json={
            "request": {
                "type": "IntentRequest",
                "requestId": "alexa-confirm",
                "intent": {"name": "AddEventIntent", "slots": {}},
            },
            "session": {"user": {"userId": "alexa-user"}},
            "context": {"System": {"device": {"deviceId": "echo-1"}}},
        },
    )
    assert "confirm" in confirm.json()["response"]["outputSpeech"]["text"].lower()

    monkeypatch.setattr(
        alexa_module.core_client,
        "send_command",
        lambda envelope: make_response("denied", "Not allowed."),
    )
    denied = client_alexa.post(
        "/alexa/webhook",
        json={
            "request": {
                "type": "IntentRequest",
                "requestId": "alexa-deny",
                "intent": {"name": "WhatTodayIntent", "slots": {}},
            },
            "session": {"user": {"userId": "alexa-user"}},
            "context": {"System": {"device": {"deviceId": "echo-1"}}},
        },
    )
    assert denied.json()["response"]["shouldEndSession"] is True

    monkeypatch.setattr(
        alexa_module.core_client,
        "send_command",
        lambda envelope: make_response("failed", "Failed."),
    )
    failed = client_alexa.post(
        "/alexa/webhook",
        json={
            "request": {
                "type": "IntentRequest",
                "requestId": "alexa-fail",
                "intent": {"name": "WhatTodayIntent", "slots": {}},
            },
            "session": {"user": {"userId": "alexa-user"}},
            "context": {"System": {"device": {"deviceId": "echo-1"}}},
        },
    )
    assert "wrong" in failed.json()["response"]["outputSpeech"]["text"].lower()

    def explode(envelope: CommandEnvelope) -> None:
        raise RuntimeError("core unavailable")

    monkeypatch.setattr(alexa_module.core_client, "send_command", explode)
    error = client_alexa.post(
        "/alexa/webhook",
        json={
            "request": {
                "type": "IntentRequest",
                "requestId": "alexa-error",
                "intent": {"name": "WhatTodayIntent", "slots": {}},
            },
            "session": {"user": {"userId": "alexa-user"}},
            "context": {"System": {"device": {"deviceId": "echo-1"}}},
        },
    )
    assert error.status_code == 200


def test_orchestrator_without_clients_and_approval_states() -> None:
    store = ApprovalStore()
    bare = CommandOrchestrator(
        policy_evaluate=evaluate_policy,
        approval_store=store,
        audit_store=AuditStore(),
    )
    actor = Actor(user_id="u1", role="adult", device_id="d1")
    add_event = CommandEnvelope(
        request_id="req-event",
        source="dashboard",
        actor=actor,
        intent=Intent(name="add_event", parameters={"title": "Dinner"}),
        context=Context(),
    )
    pending = bare.handle(add_event)
    assert pending.status == "requires_confirmation"
    store.confirm(pending.approval_id or "")
    assert bare.execute_approval(pending.approval_id or "").status == "executed"

    rejected = store.create(add_event)
    store.reject(rejected.approval_id)
    assert bare.execute_approval(rejected.approval_id).status == "failed"

    task = CommandEnvelope(
        request_id="req-task",
        source="dashboard",
        actor=actor,
        intent=Intent(name="add_task", parameters={"title": "Milk"}),
        context=Context(),
    )
    assert bare.handle(task).result["intent"] == "add_task"

    timer = CommandEnvelope(
        request_id="req-timer",
        source="dashboard",
        actor=actor,
        intent=Intent(name="set_timer", parameters={"seconds": 5}),
        context=Context(),
    )
    assert bare.handle(timer).status == "requires_confirmation"


def test_policy_voice_and_timer_rules() -> None:
    actor = Actor(user_id="u1", role="adult", device_id="d1")
    voice_task = PolicyEvaluationRequest(
        actor=actor,
        action={"type": "task_create", "resource": "tasks"},
        context={"source": "alexa"},
    )
    assert evaluate_policy(voice_task).decision == "REQUIRE_CONFIRMATION"

    timer = PolicyEvaluationRequest(
        actor=actor,
        action={"type": "set_timer", "resource": "timer"},
        context={"source": "dashboard"},
    )
    assert evaluate_policy(timer).decision == "REQUIRE_CONFIRMATION"


def test_store_missing_records() -> None:
    store = ApprovalStore()
    assert store.get("missing") is None
    assert store.confirm("missing") is None
    assert store.reject("missing") is None


def test_web_error_paths() -> None:
    rejected = client_web.post("/v1/approvals/missing/reject")
    assert rejected.status_code == 404

    denied = client_web.post(
        "/v1/calendar/events",
        json={
            "calendar_scope": "family_shared",
            "title": "Blocked",
            "start_time": "2026-07-11T19:00:00-05:00",
            "end_time": "2026-07-11T20:00:00-05:00",
        },
    )
    assert denied.status_code == 200

    with patch("services.web.app.orchestrator.handle") as mock_handle:
        mock_handle.return_value = type(
            "Resp",
            (),
            {"status": "denied", "message": "denied", "result": {}},
        )()
        bad_event = client_web.post(
            "/v1/calendar/events",
            json={
                "calendar_scope": "family_shared",
                "title": "Blocked",
                "start_time": "2026-07-11T19:00:00-05:00",
                "end_time": "2026-07-11T20:00:00-05:00",
            },
        )
        assert bad_event.status_code == 400


def test_config_defaults() -> None:
    assert Settings.from_env().core_url == "http://core:8000"
    assert Settings(alexa_skip_verify=False).alexa_skip_verify is False
    with patch.dict(os.environ, {}, clear=True):
        assert Settings.from_env().alexa_skip_verify is False


def test_remaining_coverage(monkeypatch: pytest.MonkeyPatch) -> None:
    from services.alexa_ingress import app as alexa_module
    from shared.jarvis_common.models import CommandResponse

    assert client_alexa.get("/health").json() == {"status": "ok"}
    assert client_web.get("/health").json() == {"status": "ok"}
    assert client_web.post("/v1/commands", json=_envelope("what_today")).status_code == 200

    monkeypatch.setattr(
        alexa_module.core_client,
        "send_command",
        lambda envelope: CommandResponse(
            request_id="r",
            status="executed",
            message="Done.",
        ),
    )
    assert client_alexa.post("/alexa/webhook", json=_alexa_payload("WhatTodayIntent")).status_code == 200

    store = ApprovalStore()
    bare = CommandOrchestrator(
        policy_evaluate=evaluate_policy,
        approval_store=store,
        audit_store=AuditStore(),
    )
    system = Actor(user_id="sys", role="system", device_id="d")
    assert bare.handle(
        CommandEnvelope(
            request_id="r1",
            source="scheduler",
            actor=system,
            intent=Intent(name="what_today", parameters={}),
            context=Context(),
        )
    ).result["events"] == []
    assert bare.handle(
        CommandEnvelope(
            request_id="r2",
            source="scheduler",
            actor=system,
            intent=Intent(name="set_timer", parameters={"seconds": 1}),
            context=Context(),
        )
    ).result["scheduled"] is True

    class Broken(LocalConnectorClient):
        def calendar_write(self, envelope: CommandEnvelope) -> dict[str, object]:
            raise RuntimeError("fail")

    broken = CommandOrchestrator(
        policy_evaluate=evaluate_policy,
        approval_store=store,
        audit_store=AuditStore(),
        connector_client=Broken(),
    )
    event = CommandEnvelope(
        request_id="r3",
        source="dashboard",
        actor=Actor(user_id="u", role="adult", device_id="d"),
        intent=Intent(name="add_event", parameters={"title": "X"}),
        context=Context(),
    )
    approval_id = broken.handle(event).approval_id or ""
    store.confirm(approval_id)
    assert broken.execute_approval(approval_id).status == "failed"

    create = client_core.post(
        "/v1/commands",
        json=_envelope("add_event", parameters={"title": "X", "calendar_scope": "family_shared"}),
    )
    approval_id = create.json()["approval_id"]
    client_web.post(f"/v1/approvals/{approval_id}/reject")
    assert client_web.post(f"/v1/approvals/{approval_id}/reject").json()["executed"] is False

    with patch("services.web.app.orchestrator.handle") as mock_handle:
        mock_handle.return_value = CommandResponse(
            request_id="r",
            status="denied",
            message="denied",
        )
        assert client_web.post(
            "/v1/calendar/events",
            json={
                "calendar_scope": "family_shared",
                "title": "X",
                "start_time": "2026-07-11T19:00:00-05:00",
                "end_time": "2026-07-11T20:00:00-05:00",
            },
        ).status_code == 400
        assert client_web.post("/v1/tasks", json={"list": "shopping", "title": "X"}).status_code == 400

    with patch("services.web.app.orchestrator.handle") as mock_handle:
        mock_handle.return_value = CommandResponse(
            request_id="r",
            status="executed",
            message="ok",
            result={"event_id": "e1"},
        )
        assert client_web.post(
            "/v1/calendar/events",
            json={
                "calendar_scope": "family_shared",
                "title": "X",
                "start_time": "2026-07-11T19:00:00-05:00",
                "end_time": "2026-07-11T20:00:00-05:00",
            },
        ).json()["event_id"] == "e1"

    with pytest.raises(ValueError, match="Unsupported intent"):
        bare._execute(
            CommandEnvelope.model_construct(
                request_id="r4",
                source="dashboard",
                actor=Actor(user_id="u", role="adult", device_id="d"),
                intent=Intent.model_construct(name="unknown", parameters={}),
                context=Context(),
            )
        )

    assert LocalSchedulerClient().schedule_job(
        CommandEnvelope(
            request_id="r5",
            source="dashboard",
            actor=Actor(user_id="u", role="adult", device_id="d"),
            intent=Intent(name="set_timer", parameters={"seconds": 2}),
            context=Context(),
        )
    )["seconds"] == 2
    timed = CommandOrchestrator(
        policy_evaluate=evaluate_policy,
        approval_store=ApprovalStore(),
        audit_store=AuditStore(),
        scheduler_client=LocalSchedulerClient(),
    )
    assert timed.handle(
        CommandEnvelope(
            request_id="r6",
            source="scheduler",
            actor=system,
            intent=Intent(name="set_timer", parameters={"seconds": 3}),
            context=Context(),
        )
    ).result["job_id"] == "local-job"
    assert timed._execute(
        CommandEnvelope(
            request_id="r7",
            source="scheduler",
            actor=system,
            intent=Intent(name="set_timer", parameters={"seconds": 4}),
            context=Context(),
        )
    )["seconds"] == 4
    with patch.dict(os.environ, {"JARVIS_USE_LOCAL_CLIENTS": "0"}, clear=False):
        assert isinstance(scheduler_client(), HttpSchedulerClient)


def test_alexa_unauthorized(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JARVIS_ALEXA_SKIP_VERIFY", "0")
    from shared.jarvis_common import alexa as alexa_module
    from shared.jarvis_common import config as config_module

    config_module.settings = Settings.from_env()
    alexa_module.settings = config_module.settings
    response = client_alexa.post("/alexa/webhook", json=_alexa_payload("WhatTodayIntent"))
    assert response.status_code == 401


def _alexa_payload(intent_name: str) -> dict[str, object]:
    return {
        "request": {
            "type": "IntentRequest",
            "requestId": "alexa-test",
            "intent": {"name": intent_name, "slots": {}},
        },
        "session": {"user": {"userId": "alexa-user"}},
        "context": {"System": {"device": {"deviceId": "echo-1"}}},
    }
