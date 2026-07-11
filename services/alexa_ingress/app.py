from fastapi import FastAPI, HTTPException, Request

from shared.jarvis_common.alexa import (
    alexa_error_response,
    alexa_response_for,
    parse_alexa_envelope,
    verify_alexa_request,
)
from shared.jarvis_common.clients import HttpCoreClient

app = FastAPI(title="jarvis-alexa-ingress")
core_client = HttpCoreClient()


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/alexa/webhook")
async def webhook(request: Request) -> dict:
    body = await request.body()
    headers = {key.lower(): value for key, value in request.headers.items()}

    try:
        verify_alexa_request(headers, body)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    try:
        payload = await request.json()
        envelope = parse_alexa_envelope(payload)
        command_response = core_client.send_command(envelope)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        return alexa_error_response()

    if command_response.status == "requires_confirmation":
        message = (
            f"{command_response.message} "
            "Please confirm in the Jarvis app or provide your PIN."
        )
        return alexa_response_for(message)

    if command_response.status == "denied":
        return alexa_response_for(command_response.message, end_session=True)

    if command_response.status == "failed":
        return alexa_error_response()

    return alexa_response_for(command_response.message)
