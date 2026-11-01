import uuid
from typing import Any, Dict, List

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, RedirectResponse

from services.web.dashboard import DASHBOARD_HTML
from shared.jarvis_common.bootstrap import orchestrator
from shared.jarvis_common.clients import connector_client
from shared.jarvis_common.config import settings
from shared.jarvis_common.correlation import bind_correlation
from shared.jarvis_common.email_pipeline import accept_extraction, reject_extraction
from shared.jarvis_common.email_store import email_store
from shared.jarvis_common.middleware import install_observability
from shared.jarvis_common.models import (
    ApprovalActionResponse,
    ApprovalConfirmRequest,
    CalendarEventCreateRequest,
    CommandEnvelope,
    CommandResponse,
    EmailSummaryView,
    ExtractedEventView,
    ExtractionActionRequest,
    ExtractionActionResponse,
    TaskCreateRequest,
    TimelineEvent,
)
from shared.jarvis_common.stores import approval_store, audit_store

app = FastAPI(title="jarvis-web")
install_observability(app, "jarvis-web")
_connector = connector_client()


@app.get("/", response_class=HTMLResponse)
def dashboard() -> str:
    return DASHBOARD_HTML


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/oauth/google/start")
def google_oauth_start(account_key: str = Query(default="household")) -> RedirectResponse:
    try:
        payload = _connector.oauth_start(account_key=account_key)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"OAuth start failed: {exc}") from exc
    auth_url = str(payload.get("auth_url") or "")
    if not auth_url:
        raise HTTPException(status_code=502, detail="Connector did not return auth_url.")
    return RedirectResponse(url=auth_url, status_code=302)


@app.get("/oauth/google/callback")
def google_oauth_callback(code: str = Query(...), state: str = Query(...)) -> Dict[str, Any]:
    try:
        return _connector.oauth_callback(code=code, state=state)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"OAuth callback failed: {exc}") from exc


@app.get("/oauth/google/status")
def google_oauth_status(account_key: str = Query(default="household")) -> Dict[str, Any]:
    try:
        return _connector.oauth_status(account_key=account_key)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"OAuth status failed: {exc}") from exc

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


@app.get("/v1/email/summaries", response_model=List[EmailSummaryView])
def email_summaries(limit: int = Query(default=10, ge=1, le=50)) -> List[EmailSummaryView]:
    return [
        EmailSummaryView(
            id=record.id,
            cadence=record.cadence,
            summary_text=record.summary_text,
            period_start=record.period_start.isoformat(),
            period_end=record.period_end.isoformat(),
            created_at=record.created_at.isoformat(),
            message_count=len(record.message_ids),
        )
        for record in email_store.list_summaries(limit=limit)
    ]


@app.get("/v1/extractions/pending", response_model=List[ExtractedEventView])
def pending_extractions(limit: int = Query(default=50, ge=1, le=200)) -> List[ExtractedEventView]:
    return [
        ExtractedEventView(
            id=record.id,
            title=record.title,
            start_time=record.start_time.isoformat() if record.start_time else None,
            end_time=record.end_time.isoformat() if record.end_time else None,
            location=record.location,
            confidence=record.confidence,
            status=record.status,
            raw_excerpt=record.raw_excerpt,
            created_at=record.created_at.isoformat(),
            calendar_scope=record.calendar_scope,
        )
        for record in email_store.list_extractions(status="pending", limit=limit)
    ]


@app.get("/v1/extractions/stats")
def extraction_stats() -> Dict[str, Any]:
    return email_store.extraction_stats()


@app.post("/v1/extractions/{extraction_id}/accept", response_model=ExtractionActionResponse)
def extraction_accept(extraction_id: str, payload: ExtractionActionRequest) -> ExtractionActionResponse:
    try:
        result = accept_extraction(extraction_id=extraction_id, pin=payload.confirmation_value)
    except ValueError as exc:
        raise HTTPException(status_code=404 if "not found" in str(exc).lower() else 400, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return ExtractionActionResponse(
        extraction_id=result["extraction_id"],
        status=result["status"],
        executed=bool(result.get("executed")),
        approval_id=result.get("approval_id"),
    )


@app.post("/v1/extractions/{extraction_id}/reject", response_model=ExtractionActionResponse)
def extraction_reject(extraction_id: str) -> ExtractionActionResponse:
    try:
        result = reject_extraction(extraction_id=extraction_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return ExtractionActionResponse(
        extraction_id=result["extraction_id"],
        status=result["status"],
        executed=False,
    )


@app.post("/v1/commands", response_model=CommandResponse)
def submit_command(envelope: CommandEnvelope) -> CommandResponse:
    bind_correlation(envelope.request_id)
    return orchestrator.handle(envelope)


@app.get("/v1/calendar/day")
def calendar_day(date: str = Query(..., pattern=r"^\d{4}-\d{2}-\d{2}$")) -> Dict[str, Any]:
    request_id = str(uuid.uuid4())
    bind_correlation(request_id)
    envelope = CommandEnvelope(
        request_id=request_id,
        source="dashboard",
        actor={"user_id": "dashboard-user", "role": "adult", "device_id": "dashboard"},
        intent={"name": "what_today", "parameters": {"date": date}},
        context={"timezone": "America/Chicago", "locale": "en-US"},
    )
    return _connector.calendar_read(envelope)


@app.post("/v1/calendar/events")
def create_calendar_event(payload: CalendarEventCreateRequest) -> Dict[str, Any]:
    request_id = str(uuid.uuid4())
    bind_correlation(request_id)
    envelope = CommandEnvelope(
        request_id=request_id,
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
    request_id = str(uuid.uuid4())
    bind_correlation(request_id)
    envelope = CommandEnvelope(
        request_id=request_id,
        source="dashboard",
        actor={"user_id": "dashboard-user", "role": "adult", "device_id": "dashboard"},
        intent={"name": "add_task", "parameters": payload.model_dump()},
        context={"timezone": "America/Chicago", "locale": "en-US"},
    )
    command_response = orchestrator.handle(envelope)
    if command_response.status != "executed":
        raise HTTPException(status_code=400, detail=command_response.message)
    return command_response.result
