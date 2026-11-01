from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import List, Optional
from zoneinfo import ZoneInfo

MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "sept": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}

EVENT_KEYWORDS = (
    "field trip",
    "conference",
    "early dismissal",
    "early release",
    "no school",
    "picture day",
    "open house",
    "pta",
    "permission",
    "assembly",
    "concert",
    "orientation",
)

DATE_PATTERN = re.compile(
    r"\b(?:(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\s+)?"
    r"(january|february|march|april|may|june|july|august|september|october|november|december|"
    r"jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec)\.?\s+"
    r"(\d{1,2})(?:st|nd|rd|th)?(?:,?\s+(\d{4}))?\b",
    re.IGNORECASE,
)
TIME_PATTERN = re.compile(
    r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b",
    re.IGNORECASE,
)


@dataclass
class ExtractedCandidate:
    title: str
    start_time: Optional[datetime]
    end_time: Optional[datetime]
    location: Optional[str]
    confidence: float
    raw_excerpt: str


def extract_school_events(
    *,
    subject: str,
    body: str,
    timezone: str = "America/Chicago",
    now: Optional[datetime] = None,
) -> List[ExtractedCandidate]:
    text = f"{subject}\n{body}".strip()
    if not text:
        return []
    lowered = text.lower()
    if not any(keyword in lowered for keyword in EVENT_KEYWORDS) and "school" not in lowered:
        return []

    tz = ZoneInfo(timezone)
    current = (now or datetime.now(UTC)).astimezone(tz)
    date_match = DATE_PATTERN.search(text)
    time_match = TIME_PATTERN.search(text)

    start_local: Optional[datetime] = None
    confidence = 0.45
    if date_match:
        month = MONTHS[date_match.group(2).lower()]
        day = int(date_match.group(3))
        year = int(date_match.group(4)) if date_match.group(4) else current.year
        hour, minute = 9, 0
        if time_match:
            hour, minute = _parse_clock(time_match.group(1), time_match.group(2), time_match.group(3))
            confidence += 0.2
        try:
            start_local = datetime(year, month, day, hour, minute, tzinfo=tz)
            if start_local < current - timedelta(days=1) and date_match.group(4) is None:
                start_local = start_local.replace(year=year + 1)
            confidence += 0.25
        except ValueError:
            start_local = None

    title = _title_from_subject(subject) or _title_from_keywords(lowered) or "School event"
    if any(keyword in lowered for keyword in EVENT_KEYWORDS):
        confidence += 0.1
    location = _location_from_text(text)
    if location:
        confidence += 0.05

    end_local = start_local + timedelta(hours=1) if start_local else None
    excerpt = date_match.group(0) if date_match else subject[:160]
    return [
        ExtractedCandidate(
            title=title,
            start_time=start_local.astimezone(UTC) if start_local else None,
            end_time=end_local.astimezone(UTC) if end_local else None,
            location=location,
            confidence=min(confidence, 0.99),
            raw_excerpt=excerpt.strip(),
        )
    ]


def _parse_clock(hour_raw: str, minute_raw: Optional[str], meridiem: str) -> tuple[int, int]:
    hour = int(hour_raw)
    minute = int(minute_raw or "0")
    meridiem = meridiem.lower()
    if meridiem == "pm" and hour != 12:
        hour += 12
    if meridiem == "am" and hour == 12:
        hour = 0
    return hour, minute


def _title_from_subject(subject: str) -> str:
    cleaned = re.sub(r"^(re|fw|fwd):\s*", "", subject.strip(), flags=re.IGNORECASE)
    return cleaned[:120].strip()


def _title_from_keywords(lowered: str) -> str:
    for keyword in EVENT_KEYWORDS:
        if keyword in lowered:
            return keyword.title()
    return ""


def _location_from_text(text: str) -> Optional[str]:
    match = re.search(r"\bin the ([A-Za-z0-9 .'-]{3,40})", text, re.IGNORECASE)
    if match:
        return match.group(1).strip(" .")
    match = re.search(r"\bat ([A-Za-z0-9 .'-]{3,40})(?:\.|,|$)", text, re.IGNORECASE)
    if match:
        candidate = match.group(1).strip(" .")
        if not TIME_PATTERN.search(candidate):
            return candidate
    return None
