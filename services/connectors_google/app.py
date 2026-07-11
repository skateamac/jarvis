import uuid
from typing import Any, Dict

from fastapi import FastAPI, HTTPException

from shared.jarvis_common.models import CommandEnvelope, SchedulerJobRequest, SchedulerJobResponse

app = FastAPI(title="jarvis-connectors-google")


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/internal/connectors/google/calendar/read")
def calendar_read(envelope: CommandEnvelope) -> Dict[str, Any]:
    date = envelope.intent.parameters.get("date", "today")
    return {
        "date": date,
        "events": [
            {
                "title": "Family dinner",
                "start_time": f"{date}T18:00:00-05:00",
                "calendar_scope": "family_shared",
            }
        ],
        "source": "google_connector_stub",
    }


@app.post("/internal/connectors/google/calendar/write")
def calendar_write(envelope: CommandEnvelope) -> Dict[str, Any]:
    scope = str(envelope.intent.parameters.get("calendar_scope", "family_shared"))
    if scope in {"work", "work_calendar"}:
        raise HTTPException(status_code=403, detail="Work calendar is read-only.")
    return {
        "event_id": str(uuid.uuid4()),
        "status": "created",
        "calendar_scope": scope,
        "title": envelope.intent.parameters.get("title", "Untitled event"),
    }


@app.post("/internal/connectors/google/tasks/create")
def task_create(envelope: CommandEnvelope) -> Dict[str, Any]:
    return {
        "task_id": str(uuid.uuid4()),
        "status": "created",
        "title": envelope.intent.parameters.get("title", "Untitled task"),
        "list": envelope.intent.parameters.get("list", "household"),
    }
