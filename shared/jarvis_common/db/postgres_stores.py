from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any, Dict, List, Literal, Optional

import psycopg.rows

from shared.jarvis_common.db.connection import db_connection
from shared.jarvis_common.models import CommandEnvelope
from shared.jarvis_common.stores import ApprovalRecord, AuditEvent


class PostgresApprovalStore:
    def clear(self) -> None:
        with db_connection() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM approvals")
            conn.commit()

    def create(self, envelope: CommandEnvelope) -> ApprovalRecord:
        approval_id = str(uuid.uuid4())
        created_at = datetime.now(UTC)
        with db_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO approvals (approval_id, request_id, envelope, status, created_at)
                VALUES (%s, %s, %s::jsonb, 'pending', %s)
                """,
                (approval_id, envelope.request_id, envelope.model_dump_json(), created_at),
            )
            conn.commit()
        return ApprovalRecord(
            approval_id=approval_id,
            request_id=envelope.request_id,
            envelope=envelope,
            created_at=created_at,
        )

    def get(self, approval_id: str) -> Optional[ApprovalRecord]:
        with db_connection() as conn:
            cur = conn.cursor(row_factory=psycopg.rows.dict_row)
            cur.execute(
                "SELECT approval_id, request_id, envelope, status, created_at FROM approvals WHERE approval_id = %s",
                (approval_id,),
            )
            row = cur.fetchone()
        return _approval_from_row(row) if row else None

    def list_pending(self) -> List[ApprovalRecord]:
        with db_connection() as conn:
            cur = conn.cursor(row_factory=psycopg.rows.dict_row)
            cur.execute(
                """
                SELECT approval_id, request_id, envelope, status, created_at
                FROM approvals WHERE status = 'pending' ORDER BY created_at
                """
            )
            rows = cur.fetchall()
        return [_approval_from_row(row) for row in rows]

    def confirm(self, approval_id: str) -> Optional[ApprovalRecord]:
        return self._set_status(approval_id, "approved", only_pending=True)

    def reject(self, approval_id: str) -> Optional[ApprovalRecord]:
        return self._set_status(approval_id, "rejected", only_pending=True)

    def _set_status(
        self,
        approval_id: str,
        status: Literal["approved", "rejected"],
        *,
        only_pending: bool,
    ) -> Optional[ApprovalRecord]:
        record = self.get(approval_id)
        if record is None or (only_pending and record.status != "pending"):
            return record
        with db_connection() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE approvals SET status = %s WHERE approval_id = %s",
                (status, approval_id),
            )
            conn.commit()
        record.status = status
        return record


class PostgresAuditStore:
    def clear(self) -> None:
        with db_connection() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM audit_events")
            conn.commit()

    def append(
        self,
        *,
        request_id: str,
        source: str,
        actor_id: str,
        action: str,
        outcome: str,
        detail: Optional[Dict[str, Any]] = None,
    ) -> AuditEvent:
        event = AuditEvent(
            event_id=str(uuid.uuid4()),
            request_id=request_id,
            source=source,
            actor_id=actor_id,
            action=action,
            outcome=outcome,
            detail=detail or {},
            created_at=datetime.now(UTC),
        )
        with db_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO audit_events
                    (event_id, request_id, source, actor_id, action, outcome, detail, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s)
                """,
                (
                    event.event_id,
                    event.request_id,
                    event.source,
                    event.actor_id,
                    event.action,
                    event.outcome,
                    json.dumps(event.detail),
                    event.created_at,
                ),
            )
            conn.commit()
        return event

    def list_events(self, limit: int = 50) -> List[AuditEvent]:
        with db_connection() as conn:
            cur = conn.cursor(row_factory=psycopg.rows.dict_row)
            cur.execute(
                """
                SELECT event_id, request_id, source, actor_id, action, outcome, detail, created_at
                FROM audit_events ORDER BY created_at DESC LIMIT %s
                """,
                (limit,),
            )
            rows = cur.fetchall()
        return [_audit_from_row(row) for row in rows]


def _approval_from_row(row: Dict[str, Any]) -> ApprovalRecord:
    envelope_raw = row["envelope"]
    if isinstance(envelope_raw, str):
        envelope_raw = json.loads(envelope_raw)
    return ApprovalRecord(
        approval_id=str(row["approval_id"]),
        request_id=row["request_id"],
        envelope=CommandEnvelope.model_validate(envelope_raw),
        status=row["status"],
        created_at=row["created_at"],
    )


def _audit_from_row(row: Dict[str, Any]) -> AuditEvent:
    detail = row["detail"]
    if isinstance(detail, str):
        detail = json.loads(detail)
    return AuditEvent(
        event_id=str(row["event_id"]),
        request_id=row["request_id"],
        source=row["source"],
        actor_id=row["actor_id"],
        action=row["action"],
        outcome=row["outcome"],
        detail=detail,
        created_at=row["created_at"],
    )
