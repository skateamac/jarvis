import logging

from fastapi import FastAPI, HTTPException, Request

from shared.jarvis_common.alexa import (
    alexa_error_response,
    alexa_response_for,
    parse_alexa_envelope,
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
        envelope = parse_alexa_envelope(payload)
        bind_correlation(envelope.request_id)
        command_response = core_client.send_command(envelope)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception:  # noqa: BLE001
        return alexa_error_response()

    if command_response.status == "requires_confirmation":
        message = f"{command_response.message} " "Please confirm in the Jarvis app or provide your PIN."
        return alexa_response_for(message)

    if command_response.status == "denied":
        return alexa_response_for(command_response.message, end_session=True)

    if command_response.status == "failed":
        return alexa_error_response()

    return alexa_response_for(command_response.message)
