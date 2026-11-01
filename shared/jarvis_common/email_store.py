from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Dict, List, Literal, Optional

import psycopg.rows

from shared.jarvis_common.db.connection import db_connection

ExtractionStatus = Literal["pending", "approved", "rejected", "expired"]


@dataclass
class EmailMessageRecord:
    id: str
    account_key: str
    gmail_id: str
    thread_id: Optional[str] = None
    from_addr: str = ""
    subject: str = ""
    received_at: Optional[datetime] = None
    labels: List[str] = field(default_factory=list)
    body_snippet: str = ""
    is_school: bool = False
    ingested_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass
class EmailSummaryRecord:
    id: str
    period_start: datetime
    period_end: datetime
    cadence: str
    summary_text: str
    message_ids: List[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    retention_until: Optional[datetime] = None


@dataclass
class ExtractedEventRecord:
    id: str
    title: str
    message_id: Optional[str] = None
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    location: Optional[str] = None
    confidence: float = 0.0
    status: ExtractionStatus = "pending"
    approval_id: Optional[str] = None
    calendar_scope: str = "family_shared"
    raw_excerpt: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    reviewed_at: Optional[datetime] = None
    review_actor_id: Optional[str] = None


class MemoryEmailStore:
    def __init__(self) -> None:
        self._messages: Dict[str, EmailMessageRecord] = {}
        self._summaries: Dict[str, EmailSummaryRecord] = {}
        self._extractions: Dict[str, ExtractedEventRecord] = {}
        self._gmail_index: Dict[tuple[str, str], str] = {}

    def clear(self) -> None:
        self._messages.clear()
        self._summaries.clear()
        self._extractions.clear()
        self._gmail_index.clear()

    def upsert_message(self, record: EmailMessageRecord) -> EmailMessageRecord:
        existing_id = self._gmail_index.get((record.account_key, record.gmail_id))
        if existing_id is not None:
            record.id = existing_id
        self._messages[record.id] = record
        self._gmail_index[(record.account_key, record.gmail_id)] = record.id
        return record

    def get_message(self, message_id: str) -> Optional[EmailMessageRecord]:
        return self._messages.get(message_id)

    def list_messages(
        self,
        *,
        since: Optional[datetime] = None,
        is_school: Optional[bool] = None,
        limit: int = 100,
    ) -> List[EmailMessageRecord]:
        messages = list(self._messages.values())
        if since is not None:
            messages = [m for m in messages if m.received_at is not None and m.received_at >= since]
        if is_school is not None:
            messages = [m for m in messages if m.is_school is is_school]
        messages.sort(key=lambda m: m.received_at or m.ingested_at, reverse=True)
        return messages[:limit]

    def create_summary(self, record: EmailSummaryRecord) -> EmailSummaryRecord:
        self._summaries[record.id] = record
        return record

    def list_summaries(self, *, limit: int = 20) -> List[EmailSummaryRecord]:
        summaries = sorted(self._summaries.values(), key=lambda s: s.created_at, reverse=True)
        return summaries[:limit]

    def create_extraction(self, record: ExtractedEventRecord) -> ExtractedEventRecord:
        self._extractions[record.id] = record
        return record

    def get_extraction(self, extraction_id: str) -> Optional[ExtractedEventRecord]:
        return self._extractions.get(extraction_id)

    def list_extractions(
        self,
        *,
        status: Optional[ExtractionStatus] = None,
        limit: int = 50,
    ) -> List[ExtractedEventRecord]:
        items = list(self._extractions.values())
        if status is not None:
            items = [item for item in items if item.status == status]
        items.sort(key=lambda item: item.created_at, reverse=True)
        return items[:limit]

    def update_extraction(self, record: ExtractedEventRecord) -> ExtractedEventRecord:
        self._extractions[record.id] = record
        return record

    def extraction_stats(self) -> Dict[str, Any]:
        pending = approved = rejected = 0
        for item in self._extractions.values():
            if item.status == "pending":
                pending += 1
            elif item.status == "approved":
                approved += 1
            elif item.status == "rejected":
                rejected += 1
        decided = approved + rejected
        acceptance_rate = (approved / decided) if decided else None
        return {
            "pending": pending,
            "approved": approved,
            "rejected": rejected,
            "acceptance_rate": acceptance_rate,
        }


class PostgresEmailStore:
    def clear(self) -> None:
        with db_connection() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM extracted_events")
            cur.execute("DELETE FROM email_summaries")
            cur.execute("DELETE FROM email_messages")
            conn.commit()

    def upsert_message(self, record: EmailMessageRecord) -> EmailMessageRecord:
        with db_connection() as conn, conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
            cur.execute(
                """
                INSERT INTO email_messages (
                    id, account_key, gmail_id, thread_id, from_addr, subject, received_at,
                    labels, body_snippet, is_school, ingested_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (account_key, gmail_id) DO UPDATE SET
                    thread_id = EXCLUDED.thread_id,
                    from_addr = EXCLUDED.from_addr,
                    subject = EXCLUDED.subject,
                    received_at = EXCLUDED.received_at,
                    labels = EXCLUDED.labels,
                    body_snippet = EXCLUDED.body_snippet,
                    is_school = EXCLUDED.is_school
                RETURNING id, account_key, gmail_id, thread_id, from_addr, subject, received_at,
                          labels, body_snippet, is_school, ingested_at
                """,
                (
                    record.id,
                    record.account_key,
                    record.gmail_id,
                    record.thread_id,
                    record.from_addr,
                    record.subject,
                    record.received_at,
                    record.labels,
                    record.body_snippet,
                    record.is_school,
                    record.ingested_at,
                ),
            )
            row = cur.fetchone()
            conn.commit()
        return _message_from_row(row) if row else record

    def get_message(self, message_id: str) -> Optional[EmailMessageRecord]:
        with db_connection() as conn:
            cur = conn.cursor(row_factory=psycopg.rows.dict_row)
            cur.execute(
                """
                SELECT id, account_key, gmail_id, thread_id, from_addr, subject, received_at,
                       labels, body_snippet, is_school, ingested_at
                FROM email_messages WHERE id = %s
                """,
                (message_id,),
            )
            row = cur.fetchone()
        return _message_from_row(row) if row else None

    def list_messages(
        self,
        *,
        since: Optional[datetime] = None,
        is_school: Optional[bool] = None,
        limit: int = 100,
    ) -> List[EmailMessageRecord]:
        clauses: List[str] = []
        params: List[Any] = []
        if since is not None:
            clauses.append("received_at >= %s")
            params.append(since)
        if is_school is not None:
            clauses.append("is_school = %s")
            params.append(is_school)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)
        with db_connection() as conn:
            cur = conn.cursor(row_factory=psycopg.rows.dict_row)
            cur.execute(
                f"""
                SELECT id, account_key, gmail_id, thread_id, from_addr, subject, received_at,
                       labels, body_snippet, is_school, ingested_at
                FROM email_messages
                {where}
                ORDER BY COALESCE(received_at, ingested_at) DESC
                LIMIT %s
                """,
                params,
            )
            rows = cur.fetchall()
        return [_message_from_row(row) for row in rows]

    def create_summary(self, record: EmailSummaryRecord) -> EmailSummaryRecord:
        with db_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO email_summaries (
                    id, period_start, period_end, cadence, summary_text, message_ids,
                    created_at, retention_until
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    record.id,
                    record.period_start,
                    record.period_end,
                    record.cadence,
                    record.summary_text,
                    record.message_ids,
                    record.created_at,
                    record.retention_until,
                ),
            )
            conn.commit()
        return record

    def list_summaries(self, *, limit: int = 20) -> List[EmailSummaryRecord]:
        with db_connection() as conn:
            cur = conn.cursor(row_factory=psycopg.rows.dict_row)
            cur.execute(
                """
                SELECT id, period_start, period_end, cadence, summary_text, message_ids,
                       created_at, retention_until
                FROM email_summaries ORDER BY created_at DESC LIMIT %s
                """,
                (limit,),
            )
            rows = cur.fetchall()
        return [_summary_from_row(row) for row in rows]

    def create_extraction(self, record: ExtractedEventRecord) -> ExtractedEventRecord:
        with db_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO extracted_events (
                    id, message_id, title, start_time, end_time, location, confidence,
                    status, approval_id, calendar_scope, raw_excerpt, created_at,
                    reviewed_at, review_actor_id
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    record.id,
                    record.message_id,
                    record.title,
                    record.start_time,
                    record.end_time,
                    record.location,
                    record.confidence,
                    record.status,
                    record.approval_id,
                    record.calendar_scope,
                    record.raw_excerpt,
                    record.created_at,
                    record.reviewed_at,
                    record.review_actor_id,
                ),
            )
            conn.commit()
        return record

    def get_extraction(self, extraction_id: str) -> Optional[ExtractedEventRecord]:
        with db_connection() as conn:
            cur = conn.cursor(row_factory=psycopg.rows.dict_row)
            cur.execute(
                """
                SELECT id, message_id, title, start_time, end_time, location, confidence,
                       status, approval_id, calendar_scope, raw_excerpt, created_at,
                       reviewed_at, review_actor_id
                FROM extracted_events WHERE id = %s
                """,
                (extraction_id,),
            )
            row = cur.fetchone()
        return _extraction_from_row(row) if row else None

    def list_extractions(
        self,
        *,
        status: Optional[ExtractionStatus] = None,
        limit: int = 50,
    ) -> List[ExtractedEventRecord]:
        with db_connection() as conn:
            cur = conn.cursor(row_factory=psycopg.rows.dict_row)
            if status is None:
                cur.execute(
                    """
                    SELECT id, message_id, title, start_time, end_time, location, confidence,
                           status, approval_id, calendar_scope, raw_excerpt, created_at,
                           reviewed_at, review_actor_id
                    FROM extracted_events ORDER BY created_at DESC LIMIT %s
                    """,
                    (limit,),
                )
            else:
                cur.execute(
                    """
                    SELECT id, message_id, title, start_time, end_time, location, confidence,
                           status, approval_id, calendar_scope, raw_excerpt, created_at,
                           reviewed_at, review_actor_id
                    FROM extracted_events WHERE status = %s ORDER BY created_at DESC LIMIT %s
                    """,
                    (status, limit),
                )
            rows = cur.fetchall()
        return [_extraction_from_row(row) for row in rows]

    def update_extraction(self, record: ExtractedEventRecord) -> ExtractedEventRecord:
        with db_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE extracted_events SET
                    message_id = %s, title = %s, start_time = %s, end_time = %s, location = %s,
                    confidence = %s, status = %s, approval_id = %s, calendar_scope = %s,
                    raw_excerpt = %s, reviewed_at = %s, review_actor_id = %s
                WHERE id = %s
                """,
                (
                    record.message_id,
                    record.title,
                    record.start_time,
                    record.end_time,
                    record.location,
                    record.confidence,
                    record.status,
                    record.approval_id,
                    record.calendar_scope,
                    record.raw_excerpt,
                    record.reviewed_at,
                    record.review_actor_id,
                    record.id,
                ),
            )
            conn.commit()
        return record

    def extraction_stats(self) -> Dict[str, Any]:
        with db_connection() as conn:
            cur = conn.cursor(row_factory=psycopg.rows.dict_row)
            cur.execute(
                """
                SELECT status, COUNT(*) AS count
                FROM extracted_events
                WHERE status IN ('pending', 'approved', 'rejected')
                GROUP BY status
                """
            )
            rows = cur.fetchall()
        counts = {"pending": 0, "approved": 0, "rejected": 0}
        for row in rows:
            counts[str(row["status"])] = int(row["count"])
        decided = counts["approved"] + counts["rejected"]
        return {
            **counts,
            "acceptance_rate": (counts["approved"] / decided) if decided else None,
        }


def _message_from_row(row: Dict[str, Any]) -> EmailMessageRecord:
    labels = row.get("labels") or []
    if isinstance(labels, str):
        labels = json.loads(labels)
    return EmailMessageRecord(
        id=str(row["id"]),
        account_key=row["account_key"],
        gmail_id=row["gmail_id"],
        thread_id=row.get("thread_id"),
        from_addr=row.get("from_addr") or "",
        subject=row.get("subject") or "",
        received_at=row.get("received_at"),
        labels=list(labels),
        body_snippet=row.get("body_snippet") or "",
        is_school=bool(row.get("is_school")),
        ingested_at=row.get("ingested_at") or datetime.now(UTC),
    )


def _summary_from_row(row: Dict[str, Any]) -> EmailSummaryRecord:
    message_ids = row.get("message_ids") or []
    if isinstance(message_ids, str):
        message_ids = json.loads(message_ids)
    return EmailSummaryRecord(
        id=str(row["id"]),
        period_start=row["period_start"],
        period_end=row["period_end"],
        cadence=row["cadence"],
        summary_text=row["summary_text"],
        message_ids=[str(item) for item in message_ids],
        created_at=row.get("created_at") or datetime.now(UTC),
        retention_until=row.get("retention_until"),
    )


def _extraction_from_row(row: Dict[str, Any]) -> ExtractedEventRecord:
    return ExtractedEventRecord(
        id=str(row["id"]),
        message_id=str(row["message_id"]) if row.get("message_id") else None,
        title=row["title"],
        start_time=row.get("start_time"),
        end_time=row.get("end_time"),
        location=row.get("location"),
        confidence=float(row.get("confidence") or 0),
        status=row["status"],
        approval_id=str(row["approval_id"]) if row.get("approval_id") else None,
        calendar_scope=row.get("calendar_scope") or "family_shared",
        raw_excerpt=row.get("raw_excerpt") or "",
        created_at=row.get("created_at") or datetime.now(UTC),
        reviewed_at=row.get("reviewed_at"),
        review_actor_id=row.get("review_actor_id"),
    )


def create_email_store() -> MemoryEmailStore | PostgresEmailStore:
    from shared.jarvis_common.config import settings

    if settings.database_url:
        return PostgresEmailStore()
    return MemoryEmailStore()


email_store = create_email_store()


def new_message_id() -> str:
    return str(uuid.uuid4())


def new_summary_id() -> str:
    return str(uuid.uuid4())


def new_extraction_id() -> str:
    return str(uuid.uuid4())
