from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, Query

from shared.jarvis_common.config import settings
from shared.jarvis_common.correlation import bind_correlation
from shared.jarvis_common.db.connection import db_connection
from shared.jarvis_common.db.migrate import apply_migrations
from shared.jarvis_common.job_store import job_store
from shared.jarvis_common.logging import get_logger, log_event
from shared.jarvis_common.middleware import install_observability
from shared.jarvis_common.models import CommandEnvelope, SchedulerJobRequest, SchedulerJobResponse
from shared.jarvis_common.scheduler_engine import (
    create_job,
    ensure_date_night_job,
    parse_run_at,
    process_due_jobs,
    schedule_from_envelope,
)

logger = get_logger("jarvis.scheduler")

_tick_task: Optional[asyncio.Task[None]] = None


def _autotick_enabled() -> bool:
    return os.getenv("JARVIS_SCHEDULER_AUTOTICK", "1").strip().lower() in {"1", "true", "yes", "on"}


def _tick_seconds() -> float:
    raw = os.getenv("JARVIS_SCHEDULER_TICK_SECONDS", "5")
    try:
        return max(float(raw), 0.5)
    except ValueError:
        return 5.0


async def _tick_loop() -> None:
    while True:
        process_due_jobs()
        await asyncio.sleep(_tick_seconds())


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _tick_task
    if settings.database_url:
        with db_connection() as conn:
            apply_migrations(conn)
    ensure_date_night_job()
    if _autotick_enabled():
        _tick_task = asyncio.create_task(_tick_loop())
    yield
    if _tick_task is not None:
        _tick_task.cancel()
        try:
            await _tick_task
        except asyncio.CancelledError:
            pass
        _tick_task = None


app = FastAPI(title="jarvis-scheduler", lifespan=lifespan)
install_observability(app, "jarvis-scheduler")


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "scheduled_jobs": len(job_store.list_jobs(status="scheduled"))}


@app.post("/internal/scheduler/jobs")
def schedule_from_command(envelope: CommandEnvelope) -> Dict[str, Any]:
    bind_correlation(envelope.request_id)
    result = schedule_from_envelope(envelope)
    log_event(logger, logging.INFO, "scheduler.timer_scheduled", extra={"request_id": envelope.request_id})
    return result


@app.post("/internal/scheduler/jobs/create", response_model=SchedulerJobResponse)
def create_scheduler_job(payload: SchedulerJobRequest) -> SchedulerJobResponse:
    result = create_job(
        job_type=payload.job_type,
        payload=payload.payload,
        run_at=parse_run_at(payload.run_at),
    )
    return SchedulerJobResponse(
        job_id=result["job_id"],
        status="scheduled",
        message=result["message"],
    )


@app.get("/internal/scheduler/jobs")
def list_jobs(
    status: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
) -> Dict[str, Any]:
    jobs = job_store.list_jobs(status=status, limit=limit)  # type: ignore[arg-type]
    return {
        "jobs": [
            {
                "job_id": job.job_id,
                "job_type": job.job_type,
                "status": job.status,
                "run_at": job.run_at.isoformat(),
                "payload": job.payload,
            }
            for job in jobs
        ]
    }


@app.post("/internal/scheduler/tick")
def tick() -> Dict[str, Any]:
    results = process_due_jobs()
    log_event(logger, logging.INFO, "scheduler.tick", extra={"processed": len(results)})
    return {"processed": len(results), "results": results}


@app.get("/internal/scheduler/jobs/{job_id}")
def get_job(job_id: str) -> Dict[str, Any]:
    record = job_store.get(job_id)
    if record is None:
        return {"status": "not_found", "job_id": job_id}
    return {
        "job_id": record.job_id,
        "job_type": record.job_type,
        "status": record.status,
        "run_at": record.run_at.isoformat(),
        "payload": record.payload,
    }
