from __future__ import annotations

import base64
import email.utils
from datetime import UTC, datetime
from typing import Any, Dict, List, Optional

import httpx2 as httpx

from shared.jarvis_common.config import settings
from shared.jarvis_common.google_oauth import get_access_token, oauth_status

GMAIL_API = "https://gmail.googleapis.com/gmail/v1"


def list_message_refs(
    *,
    account_key: Optional[str] = None,
    query: Optional[str] = None,
    max_results: int = 25,
) -> List[Dict[str, str]]:
    account = account_key or settings.google_account_key
    if not oauth_status(account).get("connected"):
        return _stub_message_refs()
    params: Dict[str, Any] = {
        "maxResults": max_results,
        "q": query or settings.email_gmail_query,
    }
    data = _api_get(f"{GMAIL_API}/users/me/messages", account_key=account, params=params)
    items = data.get("messages") or []
    return [{"id": str(item["id"]), "threadId": str(item.get("threadId", ""))} for item in items if item.get("id")]


def get_message(*, gmail_id: str, account_key: Optional[str] = None) -> Dict[str, Any]:
    account = account_key or settings.google_account_key
    if not oauth_status(account).get("connected"):
        return _stub_message(gmail_id)
    data = _api_get(
        f"{GMAIL_API}/users/me/messages/{gmail_id}",
        account_key=account,
        params={"format": "full"},
    )
    return normalize_message(data)


def normalize_message(raw: Dict[str, Any]) -> Dict[str, Any]:
    headers = {
        str(item.get("name", "")).lower(): str(item.get("value", ""))
        for item in (raw.get("payload") or {}).get("headers") or []
    }
    received_at = _parse_internal_date(raw.get("internalDate")) or _parse_date_header(headers.get("date"))
    snippet = str(raw.get("snippet") or "")
    body_text = _extract_body_text(raw.get("payload") or {}) or snippet
    return {
        "gmail_id": str(raw.get("id", "")),
        "thread_id": str(raw.get("threadId") or ""),
        "from_addr": headers.get("from", ""),
        "subject": headers.get("subject", ""),
        "received_at": received_at.isoformat() if received_at else None,
        "labels": list(raw.get("labelIds") or []),
        "body_snippet": snippet,
        "body_text": body_text[:4000],
    }


def is_school_sender(from_addr: str, domains: Optional[List[str]] = None) -> bool:
    configured = domains if domains is not None else _school_domains()
    if not configured:
        lowered = from_addr.lower()
        return any(token in lowered for token in ("school", "district", "pta", "classroom", ".k12."))
    address = from_addr.lower()
    return any(domain.lower() in address for domain in configured if domain)


def is_school_message(*, from_addr: str, subject: str = "", body: str = "") -> bool:
    if is_school_sender(from_addr):
        return True
    haystack = f"{subject}\n{body}".lower()
    return any(token in haystack for token in ("school", "field trip", "pta", "early dismissal", "no school"))


def _school_domains() -> List[str]:
    raw = settings.email_school_sender_domains.strip()
    if not raw:
        return []
    return [part.strip() for part in raw.split(",") if part.strip()]


def _stub_message_refs() -> List[Dict[str, str]]:
    return [
        {"id": "stub-school-1", "threadId": "stub-thread-1"},
        {"id": "stub-other-1", "threadId": "stub-thread-2"},
    ]


def _stub_message(gmail_id: str) -> Dict[str, Any]:
    if "school" in gmail_id:
        return {
            "gmail_id": gmail_id,
            "thread_id": "stub-thread-1",
            "from_addr": "office@lincoln.k12.example.edu",
            "subject": "Field Trip Permission - Friday March 14",
            "received_at": datetime.now(UTC).isoformat(),
            "labels": ["INBOX"],
            "body_snippet": "Please return permission slips for the field trip on Friday, March 14 at 9:00 AM.",
            "body_text": (
                "Dear parents, please return permission slips for the museum field trip "
                "on Friday, March 14 at 9:00 AM in the cafeteria."
            ),
        }
    return {
        "gmail_id": gmail_id,
        "thread_id": "stub-thread-2",
        "from_addr": "news@example.com",
        "subject": "Weekly newsletter",
        "received_at": datetime.now(UTC).isoformat(),
        "labels": ["INBOX"],
        "body_snippet": "This week's highlights...",
        "body_text": "This week's highlights from around the web.",
    }


def _parse_internal_date(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(int(value) / 1000, tz=UTC)
    except (TypeError, ValueError, OSError):
        return None


def _parse_date_header(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _extract_body_text(payload: Dict[str, Any]) -> str:
    mime = str(payload.get("mimeType") or "")
    body = payload.get("body") or {}
    data = body.get("data")
    if data and mime.startswith("text/plain"):
        return _decode_b64(str(data))
    parts = payload.get("parts") or []
    texts: List[str] = []
    for part in parts:
        texts.append(_extract_body_text(part))
    return "\n".join(part for part in texts if part).strip()


def _decode_b64(value: str) -> str:
    padded = value + "=" * (-len(value) % 4)
    try:
        return base64.urlsafe_b64decode(padded.encode("utf-8")).decode("utf-8", errors="replace")
    except (ValueError, UnicodeError):
        return ""


def _api_get(url: str, *, account_key: str, params: Dict[str, Any] | None = None) -> Dict[str, Any]:
    token = get_access_token(account_key)
    with httpx.Client(timeout=20.0) as client:
        response = client.get(url, params=params, headers={"Authorization": f"Bearer {token}"})
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise ValueError("Invalid Gmail API response.")
        return data
