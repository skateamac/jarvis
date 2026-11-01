from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from shared.jarvis_common.config import settings
from shared.jarvis_common.job_store import JobRecord, JobType, MemoryJobStore, PostgresJobStore, job_store
from shared.jarvis_common.models import CommandEnvelope

JobStore = MemoryJobStore | PostgresJobStore


def next_date_night_run(now: Optional[datetime] = None, *, timezone: str = "America/Chicago") -> datetime:
    """Schedule the monthly date-night reminder for the 1st at 09:00 local time."""
    tz = ZoneInfo(timezone)
    current = (now or datetime.now(UTC)).astimezone(tz)
    candidate = current.replace(day=1, hour=9, minute=0, second=0, microsecond=0)
    if candidate <= current:
        if current.month == 12:
            candidate = candidate.replace(year=current.year + 1, month=1)
        else:
            candidate = candidate.replace(month=current.month + 1)
    return candidate.astimezone(UTC)


def next_email_summary_run(
    now: Optional[datetime] = None,
    *,
    timezone: str = "America/Chicago",
    cadence: Optional[str] = None,
    hour_local: Optional[int] = None,
) -> datetime:
    """Schedule the next email summary run based on configured cadence."""
    tz = ZoneInfo(timezone)
    current = (now or datetime.now(UTC)).astimezone(tz)
    effective_cadence = (cadence or settings.email_summary_cadence).strip().lower()
    hour = hour_local if hour_local is not None else settings.email_summary_hour_local
    hour = min(max(hour, 0), 23)

    if effective_cadence == "12h":
        candidate = current.replace(minute=0, second=0, microsecond=0)
        if current.hour < 12:
            candidate = candidate.replace(hour=12)
        else:
            candidate = (candidate + timedelta(days=1)).replace(hour=0)
        if candidate <= current:
            candidate = candidate + timedelta(hours=12)
        return candidate.astimezone(UTC)

    candidate = current.replace(hour=hour, minute=0, second=0, microsecond=0)
    if effective_cadence == "weekdays":
        while True:
            if candidate > current and candidate.weekday() < 5:
                break
            candidate = candidate + timedelta(days=1)
            candidate = candidate.replace(hour=hour, minute=0, second=0, microsecond=0)
        return candidate.astimezone(UTC)

    # daily (default)
    if candidate <= current:
        candidate = candidate + timedelta(days=1)
    return candidate.astimezone(UTC)


def parse_run_at(value: Optional[str]) -> Optional[datetime]:
    if value is None:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def schedule_from_envelope(envelope: CommandEnvelope, store: JobStore | None = None) -> Dict[str, Any]:
    active = store or job_store
    seconds = int(envelope.intent.parameters.get("seconds", envelope.intent.parameters.get("duration", 60)))
    run_at = datetime.now(UTC) + timedelta(seconds=max(seconds, 0))
    record = active.create(
        job_type="timer",
        payload={
            "request_id": envelope.request_id,
            "actor_id": envelope.actor.user_id,
            "seconds": seconds,
            "message": envelope.intent.parameters.get("message", "Timer complete"),
        },
        run_at=run_at,
    )
    return _job_payload(record)


def create_job(
    *,
    job_type: JobType,
    payload: Optional[Dict[str, Any]] = None,
    run_at: Optional[datetime] = None,
    timezone: str = "America/Chicago",
    store: JobStore | None = None,
) -> Dict[str, Any]:
    active = store or job_store
    effective_run_at = run_at
    if effective_run_at is None and job_type == "date_night_check":
        effective_run_at = next_date_night_run(timezone=timezone)
    elif effective_run_at is None and job_type == "email_summary":
        effective_run_at = next_email_summary_run(timezone=timezone)
    elif effective_run_at is None:
        effective_run_at = datetime.now(UTC)
    record = active.create(job_type=job_type, payload=payload or {}, run_at=effective_run_at)
    return _job_payload(record)


def ensure_date_night_job(
    *,
    timezone: str = "America/Chicago",
    store: JobStore | None = None,
) -> Optional[Dict[str, Any]]:
    active = store or job_store
    if active.has_scheduled_type("date_night_check"):
        return None
    return create_job(
        job_type="date_night_check",
        payload={"title": "Plan date night"},
        timezone=timezone,
        store=active,
    )


def ensure_email_summary_job(
    *,
    timezone: str = "America/Chicago",
    store: JobStore | None = None,
) -> Optional[Dict[str, Any]]:
    active = store or job_store
    if active.has_scheduled_type("email_summary"):
        return None
    return create_job(
        job_type="email_summary",
        payload={"cadence": settings.email_summary_cadence, "mailboxes": settings.email_mailboxes},
        timezone=timezone,
        store=active,
    )


def process_due_jobs(
    *,
    now: Optional[datetime] = None,
    timezone: str = "America/Chicago",
    store: JobStore | None = None,
) -> List[Dict[str, Any]]:
    active = store or job_store
    current = now or datetime.now(UTC)
    results: List[Dict[str, Any]] = []
    for job in active.list_due(now=current):
        result = _execute_job(job, timezone=timezone, store=active)
        active.mark_status(job.job_id, "completed")
        results.append(result)
    return results


def _execute_job(job: JobRecord, *, timezone: str, store: JobStore) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "job_id": job.job_id,
        "job_type": job.job_type,
        "status": "completed",
        "payload": job.payload,
        "ran_at": datetime.now(UTC).isoformat(),
    }
    if job.job_type == "date_night_check":
        follow_up = create_job(
            job_type="date_night_check",
            payload=job.payload or {"title": "Plan date night"},
            run_at=next_date_night_run(timezone=timezone),
            timezone=timezone,
            store=store,
        )
        result["next_job_id"] = follow_up["job_id"]
        result["message"] = "Date-night reminder fired; next month scheduled."
    elif job.job_type == "email_summary":
        from shared.jarvis_common.email_pipeline import run_email_pipeline

        cadence = str(job.payload.get("cadence") or settings.email_summary_cadence)
        pipeline = run_email_pipeline(cadence=cadence, timezone=timezone)
        follow_up = create_job(
            job_type="email_summary",
            payload=job.payload or {"cadence": cadence},
            run_at=next_email_summary_run(timezone=timezone, cadence=cadence),
            timezone=timezone,
            store=store,
        )
        result["pipeline"] = pipeline
        result["next_job_id"] = follow_up["job_id"]
        result["message"] = "Email summary pipeline completed; next run scheduled."
    elif job.job_type == "timer":
        result["message"] = str(job.payload.get("message", "Timer complete"))
    else:
        result["message"] = f"Reminder fired: {job.payload.get('title', job.job_id)}"
    return result


def _job_payload(record: JobRecord) -> Dict[str, Any]:
    return {
        "job_id": record.job_id,
        "status": record.status,
        "job_type": record.job_type,
        "run_at": record.run_at.isoformat(),
        "payload": record.payload,
        "seconds": record.payload.get("seconds"),
        "message": f"{record.job_type} job accepted.",
    }
