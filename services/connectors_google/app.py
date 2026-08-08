from contextlib import asynccontextmanager
from typing import Any, Dict

from fastapi import FastAPI, HTTPException, Query

from shared.jarvis_common.config import settings
from shared.jarvis_common.db.connection import db_connection
from shared.jarvis_common.db.migrate import apply_migrations
from shared.jarvis_common.google_api import calendar_read as google_calendar_read
from shared.jarvis_common.google_api import calendar_write as google_calendar_write
from shared.jarvis_common.google_api import task_create as google_task_create
from shared.jarvis_common.google_oauth import (
    complete_oauth,
    oauth_status,
    refresh_oauth_token,
    start_oauth,
)
from shared.jarvis_common.models import CommandEnvelope


@asynccontextmanager
async def lifespan(app: FastAPI):
    if settings.database_url:
        with db_connection() as conn:
            apply_migrations(conn)
    yield


app = FastAPI(title="jarvis-connectors-google", lifespan=lifespan)


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "google_connected": oauth_status(settings.google_account_key).get("connected", False),
    }


@app.get("/internal/connectors/google/oauth/start")
def oauth_start(account_key: str = Query(default="household")) -> Dict[str, str]:
    try:
        return start_oauth(account_key=account_key)
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/internal/connectors/google/oauth/callback")
def oauth_callback(code: str, state: str) -> Dict[str, str]:
    try:
        record = complete_oauth(code, state)
        return {"status": "connected", "account_key": record.account_key}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail="Google token exchange failed.") from exc


@app.get("/internal/connectors/google/oauth/status")
def oauth_connection_status(account_key: str = Query(default="household")) -> Dict[str, Any]:
    return oauth_status(account_key=account_key)


@app.post("/internal/connectors/google/oauth/refresh")
def oauth_refresh(account_key: str = Query(default="household")) -> Dict[str, str]:
    try:
        record = refresh_oauth_token(account_key=account_key)
        return {"status": "refreshed", "account_key": record.account_key}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail="Google token refresh failed.") from exc


@app.post("/internal/connectors/google/calendar/read")
def calendar_read(envelope: CommandEnvelope) -> Dict[str, Any]:
    return google_calendar_read(envelope)


@app.post("/internal/connectors/google/calendar/write")
def calendar_write(envelope: CommandEnvelope) -> Dict[str, Any]:
    try:
        return google_calendar_write(envelope)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@app.post("/internal/connectors/google/tasks/create")
def task_create(envelope: CommandEnvelope) -> Dict[str, Any]:
    try:
        return google_task_create(envelope)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
