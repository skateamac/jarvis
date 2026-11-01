import logging

from fastapi import FastAPI, HTTPException, Request

from shared.jarvis_common.alexa import (
    alexa_error_response,
    alexa_request_type,
    alexa_response_for,
    launch_response,
    parse_alexa_envelope,
    session_ended_response,
    verify_alexa_request,
)
from shared.jarvis_common.clients import HttpCoreClient
from shared.jarvis_common import config as config_module
from shared.jarvis_common.correlation import bind_correlation
from shared.jarvis_common.ingress_guard import install_ingress_guard
from shared.jarvis_common.middleware import install_observability

app = FastAPI(title="jarvis-alexa-ingress")
install_observability(app, "jarvis-alexa-ingress")
install_ingress_guard(app)
core_client = HttpCoreClient()


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/alexa/webhook")
def webhook_info() -> dict:
    """Human/ops check only. Alexa always uses POST."""
    return {
        "status": "ok",
        "message": "Alexa webhook is up. Alexa must POST signed requests here; GET is only for health checks.",
        "method": "POST",
        "path": "/alexa/webhook",
    }


@app.post("/alexa/webhook")
async def webhook(request: Request) -> dict:
    body = await request.body()
    if len(body) > config_module.settings.alexa_max_body_bytes:
        raise HTTPException(status_code=413, detail="Request body too large.")
    headers = {key.lower(): value for key, value in request.headers.items()}

    try:
        verify_alexa_request(headers, body)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001
        return alexa_error_response("Sorry, I could not read that request.")

    request_type = alexa_request_type(payload)
    if request_type == "LaunchRequest":
        return launch_response()
    if request_type == "SessionEndedRequest":
        return session_ended_response()

    try:
        envelope = parse_alexa_envelope(payload)
        bind_correlation(envelope.request_id)
        command_response = core_client.send_command(envelope)
    except ValueError as exc:
        # Alexa requires a valid skill response body; bare HTTP 400s show as "unable to reach".
        return alexa_error_response(f"Sorry, I could not handle that. {exc}")
    except Exception:  # noqa: BLE001
        return alexa_error_response()

    if command_response.status == "requires_confirmation":
        message = f"{command_response.message} Please confirm in the Jarvis app or provide your PIN."
        return alexa_response_for(message)

    if command_response.status == "denied":
        return alexa_response_for(command_response.message, end_session=True)

    if command_response.status == "failed":
        return alexa_error_response(command_response.message or "Sorry, something went wrong.")

    return alexa_response_for(_speech_for_command(command_response))


def _speech_for_command(command_response) -> str:
    result = getattr(command_response, "result", None) or {}
    if isinstance(result, dict) and "events" in result:
        events = result.get("events") or []
        day = result.get("date", "today")
        if not events:
            return f"Nothing on the calendar for {day}."
        lines = []
        for event in events[:5]:
            title = event.get("title") or "Event"
            start = event.get("start_time") or ""
            if "T" in str(start):
                start = str(start).split("T", 1)[1][:5]
            lines.append(f"{title} at {start}" if start else title)
        spoken = "; ".join(lines)
        more = f" And {len(events) - 5} more." if len(events) > 5 else ""
        return f"On {day}: {spoken}.{more}"
    return command_response.message

