from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from shared.jarvis_common.config import Settings
from shared.jarvis_common.email_pipeline import (
    accept_extraction,
    reject_extraction,
    run_email_pipeline,
)
from shared.jarvis_common.email_store import (
    EmailMessageRecord,
    ExtractedEventRecord,
    MemoryEmailStore,
    new_extraction_id,
    new_message_id,
)
from shared.jarvis_common.gmail_api import (
    get_message,
    is_school_sender,
    list_message_refs,
    normalize_message,
)
from shared.jarvis_common.scheduler_engine import (
    ensure_email_summary_job,
    next_email_summary_run,
    process_due_jobs,
)
from shared.jarvis_common.school_extract import extract_school_events


def test_gmail_stubs_and_school_detection() -> None:
    refs = list_message_refs()
    assert len(refs) == 2
    school = get_message(gmail_id="stub-school-1")
    other = get_message(gmail_id="stub-other-1")
    assert "field trip" in school["body_text"].lower()
    assert is_school_sender(school["from_addr"], domains=["k12.example.edu"]) is True
    assert is_school_sender(other["from_addr"], domains=["k12.example.edu"]) is False
    assert is_school_sender("office@myschool.org", domains=[]) is True


def test_gmail_normalize_and_live_list() -> None:
    raw = {
        "id": "m1",
        "threadId": "t1",
        "snippet": "Hello",
        "internalDate": str(int(datetime(2026, 3, 14, tzinfo=UTC).timestamp() * 1000)),
        "labelIds": ["INBOX"],
        "payload": {
            "mimeType": "multipart/alternative",
            "headers": [
                {"name": "From", "value": "a@b.com"},
                {"name": "Subject", "value": "Hi"},
                {"name": "Date", "value": "Sat, 14 Mar 2026 09:00:00 -0500"},
            ],
            "parts": [
                {
                    "mimeType": "text/plain",
                    "body": {"data": "SGVsbG8gd29ybGQ"},  # "Hello world" incomplete pad ok
                }
            ],
        },
    }
    normalized = normalize_message(raw)
    assert normalized["gmail_id"] == "m1"
    assert normalized["from_addr"] == "a@b.com"
    assert normalized["subject"] == "Hi"

    settings = Settings(google_account_key="household", email_gmail_query="in:inbox")
    with patch("shared.jarvis_common.gmail_api.settings", settings):
        with patch("shared.jarvis_common.gmail_api.oauth_status", return_value={"connected": True}):
            with patch("shared.jarvis_common.gmail_api.get_access_token", return_value="token"):
                with patch("shared.jarvis_common.gmail_api.httpx.Client") as client:
                    instance = client.return_value.__enter__.return_value
                    instance.get.return_value = MagicMock(
                        raise_for_status=lambda: None,
                        json=lambda: {"messages": [{"id": "live-1", "threadId": "t-live"}]},
                    )
                    refs = list_message_refs(account_key="household")
                    assert refs == [{"id": "live-1", "threadId": "t-live"}]

                    instance.get.return_value = MagicMock(
                        raise_for_status=lambda: None,
                        json=lambda: raw,
                    )
                    message = get_message(gmail_id="m1", account_key="household")
                    assert message["gmail_id"] == "m1"

                    instance.get.return_value = MagicMock(
                        raise_for_status=lambda: None,
                        json=lambda: ["bad"],
                    )
                    with pytest.raises(ValueError, match="Invalid Gmail API response"):
                        list_message_refs(account_key="household")


def test_school_extract_parses_field_trip() -> None:
    candidates = extract_school_events(
        subject="Field Trip Permission - Friday March 14",
        body="Museum field trip on Friday, March 14 at 9:00 AM in the cafeteria.",
        timezone="America/Chicago",
        now=datetime(2026, 3, 1, tzinfo=UTC),
    )
    assert len(candidates) == 1
    assert candidates[0].title.lower().startswith("field trip")
    assert candidates[0].start_time is not None
    assert candidates[0].location == "cafeteria"
    assert candidates[0].confidence >= 0.6
    assert extract_school_events(subject="Hello", body="Nothing actionable") == []


def test_email_pipeline_stub_ingest_and_summary() -> None:
    store = MemoryEmailStore()
    result = run_email_pipeline(cadence="daily", store=store)
    assert result["ingested"] == 2
    assert result["school_messages"] >= 1
    assert result["summary_id"]
    summaries = store.list_summaries()
    assert summaries
    assert "Email summary" in summaries[0].summary_text
    pending = store.list_extractions(status="pending")
    assert pending
    stats = store.extraction_stats()
    assert stats["pending"] >= 1


def test_accept_reject_extraction_flow() -> None:
    store = MemoryEmailStore()
    run_email_pipeline(cadence="daily", store=store)
    pending = store.list_extractions(status="pending")
    assert pending
    extraction_id = pending[0].id

    with pytest.raises(PermissionError):
        accept_extraction(extraction_id=extraction_id, pin="0000", store=store)

    accepted = accept_extraction(extraction_id=extraction_id, pin="1234", store=store)
    assert accepted["status"] == "approved"
    assert accepted["executed"] is True
    assert store.get_extraction(extraction_id).status == "approved"

    # second accept is no-op
    again = accept_extraction(extraction_id=extraction_id, pin="1234", store=store)
    assert again["executed"] is False

    # create another and reject
    run_email_pipeline(cadence="daily", store=store)
    other = [item for item in store.list_extractions(status="pending")]
    assert other
    rejected = reject_extraction(extraction_id=other[0].id, store=store)
    assert rejected["status"] == "rejected"
    stats = store.extraction_stats()
    assert stats["approved"] >= 1
    assert stats["rejected"] >= 1
    assert stats["acceptance_rate"] is not None


def test_accept_missing_and_incomplete_extraction() -> None:
    store = MemoryEmailStore()
    with pytest.raises(ValueError, match="not found"):
        accept_extraction(extraction_id="missing", pin="1234", store=store)
    with pytest.raises(ValueError, match="not found"):
        reject_extraction(extraction_id="missing", store=store)

    incomplete = ExtractedEventRecord(
        id=new_extraction_id(),
        title="Incomplete",
        start_time=None,
        end_time=None,
        confidence=0.9,
        status="pending",
    )
    store.create_extraction(incomplete)
    with pytest.raises(ValueError, match="start/end"):
        accept_extraction(extraction_id=incomplete.id, pin="1234", store=store)


def test_email_summary_scheduler_helpers() -> None:
    from shared.jarvis_common.job_store import job_store

    morning = datetime(2026, 3, 10, 12, 0, tzinfo=UTC)
    nxt = next_email_summary_run(morning, timezone="America/Chicago", cadence="daily", hour_local=7)
    assert nxt > morning

    weekday = datetime(2026, 3, 14, 12, 0, tzinfo=UTC)  # Saturday
    nxt_wd = next_email_summary_run(weekday, timezone="America/Chicago", cadence="weekdays", hour_local=7)
    assert nxt_wd.astimezone(UTC).weekday() < 5

    half = next_email_summary_run(
        datetime(2026, 3, 10, 10, 0, tzinfo=UTC),
        timezone="UTC",
        cadence="12h",
        hour_local=7,
    )
    assert half.hour in {0, 12}

    seeded = ensure_email_summary_job()
    assert seeded is not None
    assert ensure_email_summary_job() is None

    past = datetime.now(UTC) - timedelta(minutes=1)
    job_store.create(job_type="email_summary", payload={"cadence": "daily"}, run_at=past)
    with patch("shared.jarvis_common.email_pipeline.run_email_pipeline", return_value={"ingested": 0}):
        results = process_due_jobs(now=datetime.now(UTC))
    assert any(item["job_type"] == "email_summary" and "next_job_id" in item for item in results)


def test_web_email_and_extraction_endpoints() -> None:
    from services.web.app import app as web_app

    client = TestClient(web_app)
    pipeline = run_email_pipeline(cadence="daily")
    assert pipeline["summary_id"]

    summaries = client.get("/v1/email/summaries").json()
    assert summaries
    assert summaries[0]["message_count"] >= 1

    pending = client.get("/v1/extractions/pending").json()
    assert pending
    extraction_id = pending[0]["id"]

    bad = client.post(
        f"/v1/extractions/{extraction_id}/accept",
        json={"confirmation_type": "pin", "confirmation_value": "0000"},
    )
    assert bad.status_code == 403

    ok = client.post(
        f"/v1/extractions/{extraction_id}/accept",
        json={"confirmation_type": "pin", "confirmation_value": "1234"},
    )
    assert ok.status_code == 200
    assert ok.json()["status"] == "approved"

    run_email_pipeline(cadence="daily")
    pending2 = client.get("/v1/extractions/pending").json()
    reject_id = pending2[0]["id"]
    rejected = client.post(f"/v1/extractions/{reject_id}/reject")
    assert rejected.json()["status"] == "rejected"

    stats = client.get("/v1/extractions/stats").json()
    assert stats["approved"] >= 1
    assert stats["rejected"] >= 1

    missing = client.post(
        "/v1/extractions/missing/accept",
        json={"confirmation_type": "pin", "confirmation_value": "1234"},
    )
    assert missing.status_code == 404
    assert client.post("/v1/extractions/missing/reject").status_code == 404

    html = client.get("/").text
    assert "School events" in html
    assert "Email summary" in html


def test_connectors_email_pipeline_endpoint() -> None:
    from services.connectors_google.app import app as connectors_app

    client = TestClient(connectors_app)
    response = client.post("/internal/connectors/google/email/pipeline")
    assert response.status_code == 200
    assert response.json()["ingested"] >= 0


def test_memory_email_store_filters() -> None:
    store = MemoryEmailStore()
    now = datetime.now(UTC)
    school = EmailMessageRecord(
        id=new_message_id(),
        account_key="household",
        gmail_id="g1",
        from_addr="school@example.com",
        subject="No school Monday",
        received_at=now,
        is_school=True,
    )
    other = EmailMessageRecord(
        id=new_message_id(),
        account_key="household",
        gmail_id="g2",
        from_addr="news@example.com",
        subject="Hello",
        received_at=now - timedelta(days=3),
        is_school=False,
    )
    store.upsert_message(school)
    store.upsert_message(other)
    # upsert same gmail id updates
    school.subject = "Updated"
    store.upsert_message(school)
    assert store.get_message(school.id).subject == "Updated"
    assert len(store.list_messages(is_school=True)) == 1
    assert len(store.list_messages(since=now - timedelta(days=1))) == 1
