from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Dict, List, Literal, Optional

from shared.jarvis_common.models import CommandEnvelope


@dataclass
class ApprovalRecord:
    approval_id: str
    request_id: str
    envelope: CommandEnvelope
    status: Literal["pending", "approved", "rejected", "expired"] = "pending"
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass
class AuditEvent:
    event_id: str
    request_id: str
    source: str
    actor_id: str
    action: str
    outcome: str
    detail: Dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class MemoryApprovalStore:
    def __init__(self) -> None:
        self._records: Dict[str, ApprovalRecord] = {}

    def clear(self) -> None:
        self._records.clear()

    def create(self, envelope: CommandEnvelope) -> ApprovalRecord:
        record = ApprovalRecord(
            approval_id=str(uuid.uuid4()),
            request_id=envelope.request_id,
            envelope=envelope,
        )
        self._records[record.approval_id] = record
        return record

    def get(self, approval_id: str) -> Optional[ApprovalRecord]:
        return self._records.get(approval_id)

    def list_pending(self) -> List[ApprovalRecord]:
        return [record for record in self._records.values() if record.status == "pending"]

    def confirm(self, approval_id: str) -> Optional[ApprovalRecord]:
        record = self._records.get(approval_id)
        if record is None or record.status != "pending":
            return record
        record.status = "approved"
        return record

    def reject(self, approval_id: str) -> Optional[ApprovalRecord]:
        record = self._records.get(approval_id)
        if record is None or record.status != "pending":
            return record
        record.status = "rejected"
        return record


class MemoryAuditStore:
    def __init__(self) -> None:
        self._events: List[AuditEvent] = []

    def clear(self) -> None:
        self._events.clear()

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
        )
        self._events.append(event)
        return event

    def list_events(self, limit: int = 50) -> List[AuditEvent]:
        return list(reversed(self._events[-limit:]))


ApprovalStore = MemoryApprovalStore
AuditStore = MemoryAuditStore


def create_stores() -> tuple[MemoryApprovalStore | object, MemoryAuditStore | object]:
    from shared.jarvis_common.config import settings

    if settings.database_url:
        from shared.jarvis_common.db.postgres_stores import (
            PostgresApprovalStore,
            PostgresAuditStore,
        )

        return PostgresApprovalStore(), PostgresAuditStore()
    return MemoryApprovalStore(), MemoryAuditStore()


approval_store, audit_store = create_stores()
