from typing import Any, Dict, Literal, Optional

from pydantic import BaseModel, Field


class Actor(BaseModel):
    user_id: str
    role: Literal["adult", "child", "system"]
    device_id: str


class Intent(BaseModel):
    name: Literal["add_event", "what_today", "add_task", "set_timer"]
    parameters: Dict[str, Any] = Field(default_factory=dict)


class Context(BaseModel):
    timezone: str = "America/Chicago"
    locale: str = "en-US"


class CommandEnvelope(BaseModel):
    request_id: str
    source: Literal["alexa", "dashboard", "scheduler"]
    actor: Actor
    intent: Intent
    context: Context


class PolicyEvaluationRequest(BaseModel):
    actor: Actor
    action: Dict[str, Any]
    context: Dict[str, Any] = Field(default_factory=dict)


class PolicyDecision(BaseModel):
    decision: Literal["ALLOW", "DENY", "REQUIRE_CONFIRMATION"]
    reason: str
    policy_version: str = "v0.1"


class TaskCreateRequest(BaseModel):
    list: Literal["household", "shopping", "personal"]
    title: str
    due_date: Optional[str] = None
    priority: Literal["low", "normal", "high"] = "normal"


class CalendarEventCreateRequest(BaseModel):
    calendar_scope: Literal["family_shared", "spouse_personal"]
    title: str
    start_time: str
    end_time: str
    location: Optional[str] = None
    notes: Optional[str] = None


class CommandResponse(BaseModel):
    request_id: str
    status: Literal["executed", "requires_confirmation", "denied", "failed"]
    message: str
    approval_id: Optional[str] = None
    result: Dict[str, Any] = Field(default_factory=dict)


class ApprovalConfirmRequest(BaseModel):
    confirmation_type: Literal["app", "pin"]
    confirmation_value: str


class ApprovalActionResponse(BaseModel):
    approval_id: str
    status: Literal["approved", "rejected", "expired"]
    executed: bool


class TimelineEvent(BaseModel):
    event_id: str
    request_id: str
    source: str
    actor_id: str
    action: str
    outcome: str
    detail: Dict[str, Any] = Field(default_factory=dict)
    created_at: str


class SchedulerJobRequest(BaseModel):
    job_type: Literal["reminder", "timer", "date_night_check"]
    payload: Dict[str, Any] = Field(default_factory=dict)
    run_at: Optional[str] = None


class SchedulerJobResponse(BaseModel):
    job_id: str
    status: Literal["scheduled", "failed"]
    message: str
