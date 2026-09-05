from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Dict, List, Literal, Optional

import psycopg.rows

from shared.jarvis_common.db.connection import db_connection

JobStatus = Literal["scheduled", "completed", "failed", "cancelled"]
JobType = Literal["reminder", "timer", "date_night_check"]


@dataclass
class JobRecord:
    job_id: str
    job_type: JobType
    payload: Dict[str, Any] = field(default_factory=dict)
    run_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    status: JobStatus = "scheduled"
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class MemoryJobStore:
    def __init__(self) -> None:
        self._jobs: Dict[str, JobRecord] = {}

    def clear(self) -> None:
        self._jobs.clear()

    def create(
        self,
        *,
        job_type: JobType,
        payload: Optional[Dict[str, Any]] = None,
        run_at: Optional[datetime] = None,
    ) -> JobRecord:
        record = JobRecord(
            job_id=str(uuid.uuid4()),
            job_type=job_type,
            payload=payload or {},
            run_at=run_at or datetime.now(UTC),
        )
        self._jobs[record.job_id] = record
        return record

    def get(self, job_id: str) -> Optional[JobRecord]:
        return self._jobs.get(job_id)

    def list_jobs(self, *, status: Optional[JobStatus] = None, limit: int = 100) -> List[JobRecord]:
        jobs = list(self._jobs.values())
        if status is not None:
            jobs = [job for job in jobs if job.status == status]
        jobs.sort(key=lambda job: job.run_at)
        return jobs[:limit]

    def list_due(self, *, now: Optional[datetime] = None) -> List[JobRecord]:
        current = now or datetime.now(UTC)
        return [
            job
            for job in self._jobs.values()
            if job.status == "scheduled" and job.run_at <= current
        ]

    def mark_status(self, job_id: str, status: JobStatus) -> Optional[JobRecord]:
        record = self._jobs.get(job_id)
        if record is None:
            return None
        record.status = status
        return record

    def has_scheduled_type(self, job_type: JobType) -> bool:
        return any(job.job_type == job_type and job.status == "scheduled" for job in self._jobs.values())


class PostgresJobStore:
    def clear(self) -> None:
        with db_connection() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM scheduler_jobs")
            conn.commit()

    def create(
        self,
        *,
        job_type: JobType,
        payload: Optional[Dict[str, Any]] = None,
        run_at: Optional[datetime] = None,
    ) -> JobRecord:
        record = JobRecord(
            job_id=str(uuid.uuid4()),
            job_type=job_type,
            payload=payload or {},
            run_at=run_at or datetime.now(UTC),
        )
        with db_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO scheduler_jobs (job_id, job_type, payload, run_at, status, created_at)
                VALUES (%s, %s, %s::jsonb, %s, %s, %s)
                """,
                (
                    record.job_id,
                    record.job_type,
                    json.dumps(record.payload),
                    record.run_at,
                    record.status,
                    record.created_at,
                ),
            )
            conn.commit()
        return record

    def get(self, job_id: str) -> Optional[JobRecord]:
        with db_connection() as conn:
            cur = conn.cursor(row_factory=psycopg.rows.dict_row)
            cur.execute(
                """
                SELECT job_id, job_type, payload, run_at, status, created_at
                FROM scheduler_jobs WHERE job_id = %s
                """,
                (job_id,),
            )
            row = cur.fetchone()
        return _job_from_row(row) if row else None

    def list_jobs(self, *, status: Optional[JobStatus] = None, limit: int = 100) -> List[JobRecord]:
        with db_connection() as conn:
            cur = conn.cursor(row_factory=psycopg.rows.dict_row)
            if status is None:
                cur.execute(
                    """
                    SELECT job_id, job_type, payload, run_at, status, created_at
                    FROM scheduler_jobs ORDER BY run_at ASC LIMIT %s
                    """,
                    (limit,),
                )
            else:
                cur.execute(
                    """
                    SELECT job_id, job_type, payload, run_at, status, created_at
                    FROM scheduler_jobs WHERE status = %s ORDER BY run_at ASC LIMIT %s
                    """,
                    (status, limit),
                )
            rows = cur.fetchall()
        return [_job_from_row(row) for row in rows]

    def list_due(self, *, now: Optional[datetime] = None) -> List[JobRecord]:
        current = now or datetime.now(UTC)
        with db_connection() as conn:
            cur = conn.cursor(row_factory=psycopg.rows.dict_row)
            cur.execute(
                """
                SELECT job_id, job_type, payload, run_at, status, created_at
                FROM scheduler_jobs
                WHERE status = 'scheduled' AND run_at <= %s
                ORDER BY run_at ASC
                """,
                (current,),
            )
            rows = cur.fetchall()
        return [_job_from_row(row) for row in rows]

    def mark_status(self, job_id: str, status: JobStatus) -> Optional[JobRecord]:
        record = self.get(job_id)
        if record is None:
            return None
        with db_connection() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE scheduler_jobs SET status = %s WHERE job_id = %s",
                (status, job_id),
            )
            conn.commit()
        record.status = status
        return record

    def has_scheduled_type(self, job_type: JobType) -> bool:
        with db_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT 1 FROM scheduler_jobs
                WHERE job_type = %s AND status = 'scheduled' LIMIT 1
                """,
                (job_type,),
            )
            return cur.fetchone() is not None


def _job_from_row(row: Dict[str, Any]) -> JobRecord:
    payload = row["payload"]
    if isinstance(payload, str):
        payload = json.loads(payload)
    return JobRecord(
        job_id=str(row["job_id"]),
        job_type=row["job_type"],
        payload=payload,
        run_at=row["run_at"],
        status=row["status"],
        created_at=row["created_at"],
    )


def create_job_store() -> MemoryJobStore | PostgresJobStore:
    from shared.jarvis_common.config import settings

    if settings.database_url:
        return PostgresJobStore()
    return MemoryJobStore()


job_store = create_job_store()
