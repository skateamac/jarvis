from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any, Dict, Optional

from shared.jarvis_common import config as config_module
from shared.jarvis_common.alexa_verify import verify_alexa_signature_headers
from shared.jarvis_common.models import Actor, CommandEnvelope, Context, Intent

ALEXA_INTENT_MAP = {
    "WhatTodayIntent": "what_today",
    "AddEventIntent": "add_event",
    "AddTaskIntent": "add_task",
    "SetTimerIntent": "set_timer",
}

_ISO_DURATION = re.compile(
    r"^P(?:(?P<days>\d+)D)?(?:T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+)S)?)?$",
    re.IGNORECASE,
)

# Kept for tests that rebind module-level settings.
settings = config_module.settings


def verify_alexa_request(headers: Dict[str, str], body: bytes) -> None:
    if settings.alexa_skip_verify:
        return

    signature = headers.get("signature") or headers.get("Signature")
    cert_url = headers.get("signaturecertchainurl") or headers.get("SignatureCertChainUrl")
    if not signature or not cert_url:
        raise ValueError("Missing Alexa signature headers.")

    if not body:
        raise ValueError("Empty Alexa request body.")

    timestamp_header = headers.get("timestamp") or headers.get("Timestamp")
    if timestamp_header:
        try:
            request_time = datetime.fromisoformat(timestamp_header.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("Invalid Alexa timestamp.") from exc
        age = datetime.now(UTC) - request_time.astimezone(UTC)
        if age.total_seconds() > 150:
            raise ValueError("Alexa request timestamp is stale.")

    # Keep signature verification settings in sync when tests rebind this module's settings.
    config_module.settings = settings
    verify_alexa_signature_headers(headers, body)


def _slot_value(slots: Dict[str, Any], name: str) -> Optional[str]:
    slot = slots.get(name)
    if not isinstance(slot, dict):
        return None
    value = slot.get("value")
    return str(value) if value is not None else None


def alexa_request_type(payload: Dict[str, Any]) -> str:
    request = payload.get("request", {})
    return str(request.get("type") or "")


def launch_response() -> Dict[str, Any]:
    return alexa_response_for(
        "Jarvis here. You can ask what's on today, add an event, add a task, or set a timer.",
        end_session=False,
    )


def session_ended_response() -> Dict[str, Any]:
    return {"version": "1.0", "response": {"shouldEndSession": True}}


def parse_alexa_envelope(payload: Dict[str, Any]) -> CommandEnvelope:
    request = payload.get("request", {})
    request_type = str(request.get("type") or "")
    if request_type != "IntentRequest":
        raise ValueError(f"Unsupported Alexa request type: {request_type or 'unknown'}")

    intent_data = request.get("intent", {})
    alexa_intent = str(intent_data.get("name", ""))
    intent_name = ALEXA_INTENT_MAP.get(alexa_intent)
    if intent_name is None:
        raise ValueError(f"Unsupported Alexa intent: {alexa_intent}")

    slots = intent_data.get("slots", {})
    parameters: Dict[str, Any] = {}
    for key in ("title", "date", "time", "duration", "list", "calendar_scope"):
        value = _slot_value(slots, key)
        if value is not None:
            parameters[key] = value

    if intent_name == "set_timer":
        seconds = _duration_to_seconds(parameters.get("duration"))
        if seconds is not None:
            parameters["seconds"] = seconds

    session = payload.get("session", {})
    user = session.get("user", {})
    device = payload.get("context", {}).get("System", {}).get("device", {})

    return CommandEnvelope(
        request_id=str(request.get("requestId", "alexa-unknown")),
        source="alexa",
        actor=Actor(
            user_id=str(user.get("userId", "alexa-household")),
            role="adult",
            device_id=str(device.get("deviceId", "alexa-device")),
        ),
        intent=Intent(name=intent_name, parameters=parameters),
        context=Context(),
    )


def _duration_to_seconds(value: Any) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    raw = str(value).strip()
    if raw.isdigit():
        return int(raw)
    match = _ISO_DURATION.match(raw)
    if not match:
        return None
    days = int(match.group("days") or 0)
    hours = int(match.group("hours") or 0)
    minutes = int(match.group("minutes") or 0)
    seconds = int(match.group("seconds") or 0)
    return days * 86400 + hours * 3600 + minutes * 60 + seconds


def alexa_response_for(command_message: str, *, end_session: bool = False) -> Dict[str, Any]:
    return {
        "version": "1.0",
        "response": {
            "outputSpeech": {"type": "PlainText", "text": command_message},
            "shouldEndSession": end_session,
        },
    }


def alexa_error_response(message: str = "Sorry, something went wrong.") -> Dict[str, Any]:
    return alexa_response_for(message, end_session=True)
