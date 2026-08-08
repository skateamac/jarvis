import uuid
from typing import Any, Dict

from fastapi import FastAPI

from shared.jarvis_common.models import CommandEnvelope, SchedulerJobRequest, SchedulerJobResponse

app = FastAPI(title="jarvis-scheduler")


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/internal/scheduler/jobs")
def schedule_from_command(envelope: CommandEnvelope) -> Dict[str, Any]:
    seconds = envelope.intent.parameters.get("seconds", envelope.intent.parameters.get("duration", 60))
    return {
        "job_id": str(uuid.uuid4()),
        "status": "scheduled",
        "job_type": "timer",
        "seconds": seconds,
    }


@app.post("/internal/scheduler/jobs/create", response_model=SchedulerJobResponse)
def create_job(payload: SchedulerJobRequest) -> SchedulerJobResponse:
    return SchedulerJobResponse(
        job_id=str(uuid.uuid4()),
        status="scheduled",
        message=f"{payload.job_type} job accepted.",
    )
