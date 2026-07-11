from __future__ import annotations

from typing import Any, Callable, Dict, Optional, Protocol

from shared.jarvis_common.models import (
    CommandEnvelope,
    CommandResponse,
    PolicyDecision,
    PolicyEvaluationRequest,
)
from shared.jarvis_common.stores import ApprovalStore, AuditStore


class ConnectorClient(Protocol):
    def calendar_read(self, envelope: CommandEnvelope) -> Dict[str, Any]: ...

    def calendar_write(self, envelope: CommandEnvelope) -> Dict[str, Any]: ...

    def task_create(self, envelope: CommandEnvelope) -> Dict[str, Any]: ...


class SchedulerClient(Protocol):
    def schedule_job(self, envelope: CommandEnvelope) -> Dict[str, Any]: ...


PolicyEvaluator = Callable[[PolicyEvaluationRequest], PolicyDecision]
CommandExecutor = Callable[[CommandEnvelope], CommandResponse]


def intent_action_type(intent_name: str) -> str:
    mapping = {
        "what_today": "calendar_read",
        "add_event": "calendar_write",
        "add_task": "task_create",
        "set_timer": "set_timer",
    }
    return mapping[intent_name]


def policy_request_for(envelope: CommandEnvelope) -> PolicyEvaluationRequest:
    parameters = envelope.intent.parameters
    resource = str(parameters.get("calendar_scope", "family_shared"))
    if envelope.intent.name == "add_event" and resource == "work":
        resource = "work_calendar"
    return PolicyEvaluationRequest(
        actor=envelope.actor,
        action={"type": intent_action_type(envelope.intent.name), "resource": resource},
        context={"source": envelope.source},
    )


class CommandOrchestrator:
    def __init__(
        self,
        *,
        policy_evaluate: PolicyEvaluator,
        approval_store: ApprovalStore,
        audit_store: AuditStore,
        connector_client: Optional[ConnectorClient] = None,
        scheduler_client: Optional[SchedulerClient] = None,
    ) -> None:
        self._policy_evaluate = policy_evaluate
        self._approval_store = approval_store
        self._audit_store = audit_store
        self._connector_client = connector_client
        self._scheduler_client = scheduler_client

    def handle(self, envelope: CommandEnvelope) -> CommandResponse:
        decision = self._policy_evaluate(policy_request_for(envelope))
        action = envelope.intent.name

        if decision.decision == "DENY":
            self._audit(
                envelope,
                action=action,
                outcome="denied",
                detail={"reason": decision.reason},
            )
            return CommandResponse(
                request_id=envelope.request_id,
                status="denied",
                message=decision.reason,
            )

        if decision.decision == "REQUIRE_CONFIRMATION":
            approval = self._approval_store.create(envelope)
            self._audit(
                envelope,
                action=action,
                outcome="requires_confirmation",
                detail={"approval_id": approval.approval_id},
            )
            return CommandResponse(
                request_id=envelope.request_id,
                status="requires_confirmation",
                message=decision.reason,
                approval_id=approval.approval_id,
            )

        try:
            result = self._execute(envelope)
        except Exception as exc:  # noqa: BLE001 - surface safe command failure
            self._audit(
                envelope,
                action=action,
                outcome="failed",
                detail={"error": str(exc)},
            )
            return CommandResponse(
                request_id=envelope.request_id,
                status="failed",
                message="Command execution failed.",
            )

        self._audit(envelope, action=action, outcome="executed", detail=result)
        return CommandResponse(
            request_id=envelope.request_id,
            status="executed",
            message=f"Command accepted: {action}.",
            result=result,
        )

    def execute_approval(self, approval_id: str) -> CommandResponse:
        record = self._approval_store.get(approval_id)
        if record is None:
            return CommandResponse(
                request_id="unknown",
                status="failed",
                message="Approval not found.",
            )
        if record.status != "approved":
            return CommandResponse(
                request_id=record.request_id,
                status="failed",
                message=f"Approval is {record.status}.",
            )

        try:
            result = self._execute(record.envelope)
        except Exception as exc:  # noqa: BLE001
            self._audit(
                record.envelope,
                action=record.envelope.intent.name,
                outcome="failed",
                detail={"error": str(exc), "approval_id": approval_id},
            )
            return CommandResponse(
                request_id=record.request_id,
                status="failed",
                message="Approved command execution failed.",
            )

        self._audit(
            record.envelope,
            action=record.envelope.intent.name,
            outcome="executed",
            detail={"approval_id": approval_id, **result},
        )
        return CommandResponse(
            request_id=record.request_id,
            status="executed",
            message="Approved command executed.",
            result=result,
        )

    def _execute(self, envelope: CommandEnvelope) -> Dict[str, Any]:
        intent = envelope.intent.name
        if intent == "what_today":
            if self._connector_client is None:
                return {"events": [], "intent": intent}
            return self._connector_client.calendar_read(envelope)
        if intent == "add_event":
            if self._connector_client is None:
                return {"created": True, "intent": intent}
            return self._connector_client.calendar_write(envelope)
        if intent == "add_task":
            if self._connector_client is None:
                return {"created": True, "intent": intent}
            return self._connector_client.task_create(envelope)
        if intent == "set_timer":
            if self._scheduler_client is None:
                return {"scheduled": True, "intent": intent}
            return self._scheduler_client.schedule_job(envelope)
        raise ValueError(f"Unsupported intent: {intent}")

    def _audit(
        self,
        envelope: CommandEnvelope,
        *,
        action: str,
        outcome: str,
        detail: Optional[Dict[str, Any]] = None,
    ) -> None:
        self._audit_store.append(
            request_id=envelope.request_id,
            source=envelope.source,
            actor_id=envelope.actor.user_id,
            action=action,
            outcome=outcome,
            detail=detail or {},
        )
