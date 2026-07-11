import uuid
from typing import Any, Dict, List

from fastapi import FastAPI, HTTPException, Query

from shared.jarvis_common.bootstrap import orchestrator
from shared.jarvis_common.clients import connector_client
from shared.jarvis_common.config import settings
from shared.jarvis_common.models import (
    ApprovalActionResponse,
    ApprovalConfirmRequest,
    CalendarEventCreateRequest,
    CommandEnvelope,
    CommandResponse,
    TaskCreateRequest,
    TimelineEvent,
)
from shared.jarvis_common.stores import approval_store, audit_store

app = FastAPI(title="jarvis-web")
_connector = connector_client()


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/v1/timeline", response_model=List[TimelineEvent])
def timeline(limit: int = Query(default=50, ge=1, le=200)) -> List[TimelineEvent]:
    return [
        TimelineEvent(
            event_id=event.event_id,
            request_id=event.request_id,
            source=event.source,
            actor_id=event.actor_id,
            action=event.action,
            outcome=event.outcome,
            detail=event.detail,
            created_at=event.created_at.isoformat(),
        )
        for event in audit_store.list_events(limit=limit)
    ]


@app.get("/v1/approvals/pending")
def pending_approvals() -> Dict[str, Any]:
    records = approval_store.list_pending()
    return {
        "approvals": [
            {
                "approval_id": record.approval_id,
                "request_id": record.request_id,
                "intent": record.envelope.intent.name,
                "parameters": record.envelope.intent.parameters,
                "created_at": record.created_at.isoformat(),
            }
            for record in records
        ]
    }


@app.post("/v1/approvals/{approval_id}/confirm", response_model=ApprovalActionResponse)
def confirm_approval(approval_id: str, payload: ApprovalConfirmRequest) -> ApprovalActionResponse:
    record = approval_store.get(approval_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Approval not found.")
    if record.status != "pending":
        return ApprovalActionResponse(
            approval_id=approval_id,
            status=record.status,
            executed=False,
        )

    if payload.confirmation_type == "pin" and payload.confirmation_value != settings.household_pin:
        raise HTTPException(status_code=403, detail="Invalid PIN.")

    approval_store.confirm(approval_id)
    execution = orchestrator.execute_approval(approval_id)
    return ApprovalActionResponse(
        approval_id=approval_id,
        status="approved",
        executed=execution.status == "executed",
    )


@app.post("/v1/approvals/{approval_id}/reject", response_model=ApprovalActionResponse)
def reject_approval(approval_id: str) -> ApprovalActionResponse:
    record = approval_store.get(approval_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Approval not found.")
    if record.status != "pending":
        return ApprovalActionResponse(
            approval_id=approval_id,
            status=record.status,
            executed=False,
        )

    approval_store.reject(approval_id)
    audit_store.append(
        request_id=record.request_id,
        source=record.envelope.source,
        actor_id=record.envelope.actor.user_id,
        action=record.envelope.intent.name,
        outcome="rejected",
        detail={"approval_id": approval_id},
    )
    return ApprovalActionResponse(
        approval_id=approval_id,
        status="rejected",
        executed=False,
    )


@app.post("/v1/commands", response_model=CommandResponse)
def submit_command(envelope: CommandEnvelope) -> CommandResponse:
    return orchestrator.handle(envelope)


@app.get("/v1/calendar/day")
def calendar_day(date: str = Query(..., pattern=r"^\d{4}-\d{2}-\d{2}$")) -> Dict[str, Any]:
    envelope = CommandEnvelope(
        request_id=str(uuid.uuid4()),
        source="dashboard",
        actor={"user_id": "dashboard-user", "role": "adult", "device_id": "dashboard"},
        intent={"name": "what_today", "parameters": {"date": date}},
        context={"timezone": "America/Chicago", "locale": "en-US"},
    )
    return _connector.calendar_read(envelope)


@app.post("/v1/calendar/events")
def create_calendar_event(payload: CalendarEventCreateRequest) -> Dict[str, Any]:
    envelope = CommandEnvelope(
        request_id=str(uuid.uuid4()),
        source="dashboard",
        actor={"user_id": "dashboard-user", "role": "adult", "device_id": "dashboard"},
        intent={"name": "add_event", "parameters": payload.model_dump()},
        context={"timezone": "America/Chicago", "locale": "en-US"},
    )
    command_response = orchestrator.handle(envelope)
    if command_response.status == "requires_confirmation":
        return {
            "status": command_response.status,
            "approval_id": command_response.approval_id,
            "message": command_response.message,
        }
    if command_response.status != "executed":
        raise HTTPException(status_code=400, detail=command_response.message)
    return command_response.result


@app.post("/v1/tasks")
def create_task(payload: TaskCreateRequest) -> Dict[str, Any]:
    envelope = CommandEnvelope(
        request_id=str(uuid.uuid4()),
        source="dashboard",
        actor={"user_id": "dashboard-user", "role": "adult", "device_id": "dashboard"},
        intent={"name": "add_task", "parameters": payload.model_dump()},
        context={"timezone": "America/Chicago", "locale": "en-US"},
    )
    command_response = orchestrator.handle(envelope)
    if command_response.status != "executed":
        raise HTTPException(status_code=400, detail=command_response.message)
    return command_response.result
