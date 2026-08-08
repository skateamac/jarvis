from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any, Dict, List
from zoneinfo import ZoneInfo

import httpx2 as httpx

from shared.jarvis_common.config import settings
from shared.jarvis_common.google_oauth import get_access_token, oauth_status
from shared.jarvis_common.models import CommandEnvelope

CALENDAR_API = "https://www.googleapis.com/calendar/v3"
TASKS_API = "https://tasks.googleapis.com/tasks/v1"
READ_SCOPES = ("family_shared", "spouse_personal", "work")
WRITE_SCOPES = ("family_shared", "spouse_personal")
LIST_MAP = {
    "household": "google_tasks_list_household",
    "shopping": "google_tasks_list_shopping",
    "personal": "google_tasks_list_personal",
}


def calendar_read(envelope: CommandEnvelope) -> Dict[str, Any]:
    if not oauth_status(settings.google_account_key).get("connected"):
        return _stub_calendar_read(envelope)
    date = str(envelope.intent.parameters.get("date", "today"))
    tz = ZoneInfo(envelope.context.timezone)
    day = _resolve_date(date, tz)
    start = datetime.combine(day, datetime.min.time(), tzinfo=tz)
    end = start + timedelta(days=1)
    events: List[Dict[str, Any]] = []
    for scope in READ_SCOPES:
        calendar_id = _calendar_id(scope)
        if not calendar_id:
            continue
        for item in _list_events(calendar_id, start, end, tz):
            events.append(_normalize_event(item, scope))
    events.sort(key=lambda event: event["start_time"])
    return {"date": day.isoformat(), "events": events, "source": "google_calendar_api"}


def calendar_write(envelope: CommandEnvelope) -> Dict[str, Any]:
    scope = str(envelope.intent.parameters.get("calendar_scope", "family_shared"))
    if scope in {"work", "work_calendar"}:
        raise ValueError("Work calendar is read-only.")
    if scope not in WRITE_SCOPES:
        raise ValueError(f"Unsupported calendar scope: {scope}")
    if not oauth_status(settings.google_account_key).get("connected"):
        return _stub_calendar_write(envelope, scope)
    calendar_id = _calendar_id(scope)
    if not calendar_id:
        raise ValueError(f"Calendar not configured for scope: {scope}")
    payload = _event_payload(envelope, scope)
    created = _api_post(f"{CALENDAR_API}/calendars/{calendar_id}/events", payload)
    return {
        "event_id": created.get("id", "unknown"),
        "status": "created",
        "calendar_scope": scope,
        "title": payload["summary"],
        "html_link": created.get("htmlLink"),
    }


def task_create(envelope: CommandEnvelope) -> Dict[str, Any]:
    list_name = str(envelope.intent.parameters.get("list", "household"))
    title = str(envelope.intent.parameters.get("title", "Untitled task"))
    if not oauth_status(settings.google_account_key).get("connected"):
        return {"task_id": "stub-task", "status": "created", "title": title, "list": list_name}
    tasklist_id = _tasklist_id(list_name)
    payload: Dict[str, Any] = {"title": title}
    due_date = envelope.intent.parameters.get("due_date")
    if due_date:
        payload["due"] = f"{due_date}T00:00:00.000Z"
    created = _api_post(f"{TASKS_API}/lists/{tasklist_id}/tasks", payload)
    return {
        "task_id": created.get("id", "unknown"),
        "status": "created",
        "title": title,
        "list": list_name,
        "html_link": created.get("selfLink"),
    }


def _stub_calendar_read(envelope: CommandEnvelope) -> Dict[str, Any]:
    date = str(envelope.intent.parameters.get("date", "today"))
    return {
        "date": date,
        "events": [
            {
                "title": "Family dinner",
                "start_time": f"{date}T18:00:00-05:00",
                "calendar_scope": "family_shared",
            }
        ],
        "source": "google_connector_stub",
    }


def _stub_calendar_write(envelope: CommandEnvelope, scope: str) -> Dict[str, Any]:
    return {
        "event_id": "stub-event",
        "status": "created",
        "calendar_scope": scope,
        "title": envelope.intent.parameters.get("title", "Untitled event"),
    }


def _calendar_id(scope: str) -> str:
    if scope in {"work", "work_calendar"}:
        scope = "work"
    mapping = {
        "family_shared": settings.google_calendar_family_id,
        "spouse_personal": settings.google_calendar_spouse_id,
        "work": settings.google_calendar_work_id,
    }
    return mapping.get(scope, "")


def _tasklist_id(list_name: str) -> str:
    attr = LIST_MAP.get(list_name)
    if attr and getattr(settings, attr):
        return getattr(settings, attr)
    lists = _api_get(f"{TASKS_API}/users/@me/lists").get("items", [])
    for item in lists:
        if str(item.get("title", "")).lower() == list_name.lower():
            return str(item["id"])
    created = _api_post(f"{TASKS_API}/users/@me/lists", {"title": list_name.title()})
    return str(created["id"])


def _resolve_date(raw: str, tz: ZoneInfo) -> date:
    if raw == "today":
        return datetime.now(tz).date()
    return date.fromisoformat(raw)


def _event_payload(envelope: CommandEnvelope, scope: str) -> Dict[str, Any]:
    params = envelope.intent.parameters
    title = str(params.get("title", "Untitled event"))
    tz = envelope.context.timezone
    start_time = params.get("start_time")
    end_time = params.get("end_time")
    if not start_time:
        day = str(params.get("date", "today"))
        resolved = _resolve_date(day, ZoneInfo(tz))
        start_time = f"{resolved.isoformat()}T09:00:00"
        end_time = f"{resolved.isoformat()}T10:00:00"
    payload: Dict[str, Any] = {
        "summary": title,
        "start": _time_payload(str(start_time), tz),
        "end": _time_payload(str(end_time), tz),
    }
    if params.get("location"):
        payload["location"] = params["location"]
    if params.get("notes"):
        payload["description"] = params["notes"]
    if scope:
        payload["extendedProperties"] = {"private": {"jarvis_scope": scope}}
    return payload


def _time_payload(value: str, tz: str) -> Dict[str, str]:
    if value.endswith("Z") or "+" in value[10:] or "-" in value[10:]:
        return {"dateTime": value, "timeZone": tz}
    return {"dateTime": f"{value}:00", "timeZone": tz}


def _list_events(calendar_id: str, start: datetime, end: datetime, tz: ZoneInfo) -> List[Dict[str, Any]]:
    params = {
        "timeMin": start.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "timeMax": end.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "singleEvents": "true",
        "orderBy": "startTime",
    }
    data = _api_get(f"{CALENDAR_API}/calendars/{calendar_id}/events", params=params)
    return list(data.get("items", []))


def _normalize_event(item: Dict[str, Any], scope: str) -> Dict[str, Any]:
    start = item.get("start", {})
    end = item.get("end", {})
    return {
        "event_id": item.get("id"),
        "title": item.get("summary", "Untitled"),
        "start_time": start.get("dateTime") or start.get("date"),
        "end_time": end.get("dateTime") or end.get("date"),
        "location": item.get("location"),
        "calendar_scope": scope,
    }


def _api_get(url: str, params: Dict[str, Any] | None = None) -> Dict[str, Any]:
    token = get_access_token(settings.google_account_key)
    with httpx.Client(timeout=20.0) as client:
        response = client.get(url, params=params, headers={"Authorization": f"Bearer {token}"})
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise ValueError("Invalid Google API response.")
        return data


def _api_post(url: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    token = get_access_token(settings.google_account_key)
    with httpx.Client(timeout=20.0) as client:
        response = client.post(url, json=payload, headers={"Authorization": f"Bearer {token}"})
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise ValueError("Invalid Google API response.")
        return data
