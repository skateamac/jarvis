from shared.jarvis_common.config import settings
from shared.jarvis_common.models import Actor, PolicyDecision, PolicyEvaluationRequest

WORK_CALENDAR_RESOURCES = {"work", "work_calendar"}


def evaluate_policy(payload: PolicyEvaluationRequest) -> PolicyDecision:
    action_type = payload.action.get("type", "")
    resource = str(payload.action.get("resource", "")).lower()
    source = str(payload.context.get("source", "")).lower()
    actor: Actor = payload.actor

    if action_type == "calendar_write" and resource in WORK_CALENDAR_RESOURCES:
        return PolicyDecision(
            decision="DENY",
            reason="Work calendar is read-only.",
            policy_version=settings.policy_version,
        )

    if actor.role == "child":
        if action_type in {"calendar_write", "task_create", "set_timer"}:
            return PolicyDecision(
                decision="DENY",
                reason="Child accounts cannot perform this action.",
                policy_version=settings.policy_version,
            )

    if action_type == "calendar_write":
        return PolicyDecision(
            decision="REQUIRE_CONFIRMATION",
            reason="Calendar writes require confirmation.",
            policy_version=settings.policy_version,
        )

    if action_type == "task_create" and source == "alexa":
        return PolicyDecision(
            decision="REQUIRE_CONFIRMATION",
            reason="Task creation via voice requires confirmation.",
            policy_version=settings.policy_version,
        )

    if action_type == "set_timer" and actor.role != "system":
        return PolicyDecision(
            decision="REQUIRE_CONFIRMATION",
            reason="Timers require confirmation outside system jobs.",
            policy_version=settings.policy_version,
        )

    return PolicyDecision(
        decision="ALLOW",
        reason="Action permitted by household policy.",
        policy_version=settings.policy_version,
    )
