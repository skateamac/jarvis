from typing import Any, Dict

from shared.jarvis_common.models import CommandEnvelope

INTENT_ACTION_MAP: Dict[str, str] = {
    "what_today": "calendar_read",
    "add_event": "calendar_write",
    "add_task": "task_create",
    "set_timer": "scheduler_job",
}


def envelope_to_action(envelope: CommandEnvelope) -> Dict[str, Any]:
    action_type = INTENT_ACTION_MAP.get(envelope.intent.name, envelope.intent.name)
    resource = envelope.intent.parameters.get("calendar_scope", "family_shared")
    if envelope.intent.name == "add_task":
        resource = envelope.intent.parameters.get("list", "household")
    return {"type": action_type, "resource": resource}
