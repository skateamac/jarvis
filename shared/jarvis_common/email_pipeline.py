from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Dict, List, Optional

from shared.jarvis_common.config import settings
from shared.jarvis_common.email_store import (
    EmailMessageRecord,
    EmailSummaryRecord,
    ExtractedEventRecord,
    email_store,
    new_extraction_id,
    new_message_id,
    new_summary_id,
)
from shared.jarvis_common.gmail_api import get_message, is_school_message, list_message_refs
from shared.jarvis_common.logging import get_logger, log_event
from shared.jarvis_common.school_extract import extract_school_events

logger = get_logger("jarvis.email")


def mailbox_keys() -> List[str]:
    raw = settings.email_mailboxes.strip() or settings.google_account_key
    return [part.strip() for part in raw.split(",") if part.strip()]


def run_email_pipeline(
    *,
    cadence: Optional[str] = None,
    account_keys: Optional[List[str]] = None,
    timezone: str = "America/Chicago",
    store=None,
) -> Dict[str, Any]:
    active = store or email_store
    accounts = account_keys or mailbox_keys()
    effective_cadence = cadence or settings.email_summary_cadence
    ingested: List[EmailMessageRecord] = []
    for account_key in accounts:
        ingested.extend(_ingest_mailbox(account_key=account_key, store=active))
    school_messages = [message for message in ingested if message.is_school]
    extractions = _extract_from_messages(school_messages, timezone=timezone, store=active)
    summary = _build_summary(
        messages=ingested,
        cadence=effective_cadence,
        store=active,
    )
    result = {
        "ingested": len(ingested),
        "school_messages": len(school_messages),
        "extractions_created": len(extractions),
        "summary_id": summary.id if summary else None,
        "cadence": effective_cadence,
        "accounts": accounts,
    }
    log_event(logger, logging.INFO, "email.pipeline.completed", extra=result)
    return result


def _ingest_mailbox(*, account_key: str, store) -> List[EmailMessageRecord]:
    refs = list_message_refs(account_key=account_key, query=settings.email_gmail_query)
    records: List[EmailMessageRecord] = []
    for ref in refs:
        payload = get_message(gmail_id=ref["id"], account_key=account_key)
        received_at = _parse_iso(payload.get("received_at"))
        from_addr = str(payload.get("from_addr") or "")
        subject = str(payload.get("subject") or "")
        body_snippet = str(payload.get("body_snippet") or "")
        body_text = str(payload.get("body_text") or body_snippet)
        if body_text and len(body_text) > len(body_snippet):
            body_snippet = body_text[:2000]
        record = EmailMessageRecord(
            id=new_message_id(),
            account_key=account_key,
            gmail_id=str(payload.get("gmail_id") or ref["id"]),
            thread_id=payload.get("thread_id") or ref.get("threadId"),
            from_addr=from_addr,
            subject=subject,
            received_at=received_at,
            labels=list(payload.get("labels") or []),
            body_snippet=body_snippet,
            is_school=is_school_message(from_addr=from_addr, subject=subject, body=body_snippet),
        )
        records.append(store.upsert_message(record))
    return records


def _extract_from_messages(
    messages: List[EmailMessageRecord],
    *,
    timezone: str,
    store,
) -> List[ExtractedEventRecord]:
    created: List[ExtractedEventRecord] = []
    min_confidence = settings.email_extract_min_confidence
    for message in messages:
        candidates = extract_school_events(
            subject=message.subject,
            body=message.body_snippet,
            timezone=timezone,
        )
        for candidate in candidates:
            if candidate.confidence < min_confidence:
                continue
            record = ExtractedEventRecord(
                id=new_extraction_id(),
                message_id=message.id,
                title=candidate.title,
                start_time=candidate.start_time,
                end_time=candidate.end_time,
                location=candidate.location,
                confidence=candidate.confidence,
                status="pending",
                raw_excerpt=candidate.raw_excerpt,
            )
            created.append(store.create_extraction(record))
    return created


def _build_summary(*, messages: List[EmailMessageRecord], cadence: str, store) -> Optional[EmailSummaryRecord]:
    now = datetime.now(UTC)
    period_start = now - timedelta(days=1 if cadence != "12h" else 0, hours=12 if cadence == "12h" else 0)
    if not messages:
        summary_text = "No new messages in the selected mailboxes."
    else:
        lines = [f"Email summary ({cadence}): {len(messages)} message(s)."]
        by_sender: Dict[str, int] = {}
        for message in messages:
            key = message.from_addr or "unknown"
            by_sender[key] = by_sender.get(key, 0) + 1
            marker = " [school]" if message.is_school else ""
            lines.append(f"- {message.subject or '(no subject)'}{marker} — {message.from_addr}")
        top_senders = sorted(by_sender.items(), key=lambda item: item[1], reverse=True)[:5]
        if top_senders:
            lines.append("Top senders: " + ", ".join(f"{sender} ({count})" for sender, count in top_senders))
        summary_text = "\n".join(lines)

    retention_until = now + timedelta(days=max(settings.email_summary_retention_days, 1))
    record = EmailSummaryRecord(
        id=new_summary_id(),
        period_start=period_start,
        period_end=now,
        cadence=cadence,
        summary_text=summary_text,
        message_ids=[message.id for message in messages],
        retention_until=retention_until,
    )
    return store.create_summary(record)


def accept_extraction(
    *,
    extraction_id: str,
    pin: str,
    actor_id: str = "dashboard-user",
    store=None,
    orchestrator=None,
) -> Dict[str, Any]:
    from shared.jarvis_common.bootstrap import orchestrator as default_orchestrator
    from shared.jarvis_common.models import Actor, CommandEnvelope, Context, Intent
    from shared.jarvis_common.stores import approval_store, audit_store

    active = store or email_store
    engine = orchestrator or default_orchestrator
    record = active.get_extraction(extraction_id)
    if record is None:
        raise ValueError("Extraction not found.")
    if record.status != "pending":
        return {"extraction_id": extraction_id, "status": record.status, "executed": False}
    if pin != settings.household_pin:
        raise PermissionError("Invalid PIN.")
    if record.start_time is None or record.end_time is None:
        raise ValueError("Extraction is missing start/end times.")

    request_id = str(uuid.uuid4())
    envelope = CommandEnvelope(
        request_id=request_id,
        source="dashboard",
        actor=Actor(user_id=actor_id, role="adult", device_id="dashboard"),
        intent=Intent(
            name="add_event",
            parameters={
                "title": record.title,
                "calendar_scope": record.calendar_scope,
                "start_time": record.start_time.isoformat(),
                "end_time": record.end_time.isoformat(),
                "location": record.location,
                "notes": f"Extracted from email: {record.raw_excerpt}",
            },
        ),
        context=Context(timezone="America/Chicago", locale="en-US"),
    )
    command_response = engine.handle(envelope)
    executed = False
    approval_id = command_response.approval_id
    if command_response.status == "requires_confirmation" and approval_id:
        approval_store.confirm(approval_id)
        execution = engine.execute_approval(approval_id)
        executed = execution.status == "executed"
    elif command_response.status == "executed":
        executed = True

    record.status = "approved"
    record.approval_id = approval_id
    record.reviewed_at = datetime.now(UTC)
    record.review_actor_id = actor_id
    active.update_extraction(record)
    audit_store.append(
        request_id=request_id,
        source="dashboard",
        actor_id=actor_id,
        action="school_extract.accept",
        outcome="approved" if executed else command_response.status,
        detail={"extraction_id": extraction_id, "approval_id": approval_id},
    )
    return {
        "extraction_id": extraction_id,
        "status": "approved",
        "executed": executed,
        "approval_id": approval_id,
    }


def reject_extraction(
    *,
    extraction_id: str,
    actor_id: str = "dashboard-user",
    store=None,
) -> Dict[str, Any]:
    from shared.jarvis_common.stores import audit_store

    active = store or email_store
    record = active.get_extraction(extraction_id)
    if record is None:
        raise ValueError("Extraction not found.")
    if record.status != "pending":
        return {"extraction_id": extraction_id, "status": record.status, "executed": False}

    record.status = "rejected"
    record.reviewed_at = datetime.now(UTC)
    record.review_actor_id = actor_id
    active.update_extraction(record)
    audit_store.append(
        request_id=str(uuid.uuid4()),
        source="dashboard",
        actor_id=actor_id,
        action="school_extract.reject",
        outcome="rejected",
        detail={"extraction_id": extraction_id},
    )
    return {"extraction_id": extraction_id, "status": "rejected", "executed": False}


def _parse_iso(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return None
