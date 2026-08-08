from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from shared.jarvis_common.config import Settings
from shared.jarvis_common.db.oauth_store import MemoryOAuthTokenStore, OAuthTokenRecord
from shared.jarvis_common.google_api import calendar_read, calendar_write, task_create
from shared.jarvis_common.google_oauth import get_access_token
from shared.jarvis_common.models import Actor, CommandEnvelope, Context, Intent


def _envelope(**params: object) -> CommandEnvelope:
    return CommandEnvelope(
        request_id="r1",
        source="dashboard",
        actor=Actor(user_id="u1", role="adult", device_id="d1"),
        intent=Intent(name="what_today", parameters=params),
        context=Context(timezone="America/Chicago", locale="en-US"),
    )


def test_google_api_stubs_when_disconnected() -> None:
    assert calendar_read(_envelope(date="2026-07-11"))["source"] == "google_connector_stub"
    write_env = CommandEnvelope(
        request_id="r2",
        source="dashboard",
        actor=Actor(user_id="u1", role="adult", device_id="d1"),
        intent=Intent(
            name="add_event",
            parameters={
                "title": "Dinner",
                "calendar_scope": "family_shared",
                "start_time": "2026-07-11T19:00:00-05:00",
                "end_time": "2026-07-11T20:00:00-05:00",
            },
        ),
        context=Context(),
    )
    assert calendar_write(write_env)["status"] == "created"
    task_env = CommandEnvelope(
        request_id="r3",
        source="dashboard",
        actor=Actor(user_id="u1", role="adult", device_id="d1"),
        intent=Intent(name="add_task", parameters={"title": "Milk", "list": "shopping"}),
        context=Context(),
    )
    assert task_create(task_env)["task_id"] == "stub-task"


def test_google_api_live_paths() -> None:
    store = MemoryOAuthTokenStore()
    store.upsert(
        OAuthTokenRecord(
            provider="google",
            account_key="household",
            access_token="token",
            refresh_token="refresh",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            scopes=["calendar", "tasks"],
            metadata={},
        )
    )
    settings = Settings(
        google_account_key="household",
        google_calendar_family_id="family-cal",
        google_calendar_spouse_id="spouse-cal",
        google_calendar_work_id="work-cal",
        google_tasks_list_shopping="shop-list",
    )

    def fake_get(url: str, params=None, headers=None):
        if url.endswith("/events"):
            return MagicMock(
                raise_for_status=lambda: None,
                json=lambda: {
                    "items": [
                        {
                            "id": "e1",
                            "summary": "Meeting",
                            "start": {"dateTime": "2026-07-11T10:00:00-05:00"},
                            "end": {"dateTime": "2026-07-11T11:00:00-05:00"},
                        }
                    ]
                },
            )
        return MagicMock(raise_for_status=lambda: None, json=lambda: {"items": []})

    def fake_post(url: str, json=None, headers=None):
        if url.endswith("/events"):
            return MagicMock(
                raise_for_status=lambda: None,
                json=lambda: {"id": "evt-1", "htmlLink": "http://cal"},
            )
        return MagicMock(raise_for_status=lambda: None, json=lambda: {"id": "task-1", "selfLink": "http://task"})

    with patch("shared.jarvis_common.google_api.settings", settings):
        with patch("shared.jarvis_common.google_api.oauth_status", return_value={"connected": True}):
            with patch("shared.jarvis_common.google_api.get_access_token", return_value="token"):
                with patch("shared.jarvis_common.google_api.httpx.Client") as client:
                    client.return_value.__enter__.return_value.get.side_effect = fake_get
                    client.return_value.__enter__.return_value.post.side_effect = fake_post
                    read = calendar_read(_envelope(date="2026-07-11"))
                    assert read["source"] == "google_calendar_api"
                    assert read["events"][0]["title"] == "Meeting"
                    created = calendar_write(
                        CommandEnvelope(
                            request_id="r4",
                            source="dashboard",
                            actor=Actor(user_id="u1", role="adult", device_id="d1"),
                            intent=Intent(
                                name="add_event",
                                parameters={
                                    "title": "Dinner",
                                    "calendar_scope": "family_shared",
                                    "start_time": "2026-07-11T19:00:00-05:00",
                                    "end_time": "2026-07-11T20:00:00-05:00",
                                },
                            ),
                            context=Context(),
                        )
                    )
                    assert created["event_id"] == "evt-1"
                    task = task_create(
                        CommandEnvelope(
                            request_id="r5",
                            source="dashboard",
                            actor=Actor(user_id="u1", role="adult", device_id="d1"),
                            intent=Intent(
                                name="add_task",
                                parameters={
                                    "title": "Milk",
                                    "list": "shopping",
                                    "due_date": "2026-07-12",
                                },
                            ),
                            context=Context(),
                        )
                    )
                    assert task["task_id"] == "task-1"


def test_google_api_errors_and_helpers() -> None:
    with pytest.raises(ValueError, match="read-only"):
        calendar_write(
            CommandEnvelope(
                request_id="r6",
                source="dashboard",
                actor=Actor(user_id="u1", role="adult", device_id="d1"),
                intent=Intent(name="add_event", parameters={"calendar_scope": "work"}),
                context=Context(),
            )
        )
    settings = Settings(google_account_key="household", google_calendar_family_id="")
    with patch("shared.jarvis_common.google_api.settings", settings):
        with patch("shared.jarvis_common.google_api.oauth_status", return_value={"connected": True}):
            with pytest.raises(ValueError, match="not configured"):
                calendar_write(
                    CommandEnvelope(
                        request_id="r7",
                        source="dashboard",
                        actor=Actor(user_id="u1", role="adult", device_id="d1"),
                        intent=Intent(
                            name="add_event",
                            parameters={"title": "X", "calendar_scope": "family_shared"},
                        ),
                        context=Context(),
                    )
                )


def test_get_access_token_refresh() -> None:
    store = MemoryOAuthTokenStore()
    store.upsert(
        OAuthTokenRecord(
            provider="google",
            account_key="household",
            access_token="old",
            refresh_token="refresh",
            expires_at=datetime.now(UTC) - timedelta(minutes=5),
            scopes=[],
            metadata={},
        )
    )
    with patch("shared.jarvis_common.google_oauth.oauth_token_store", store):
        with patch(
            "shared.jarvis_common.google_oauth.refresh_oauth_token",
            return_value=store.get("google", "household"),
        ):
            assert get_access_token() == "old"
    with patch("shared.jarvis_common.google_oauth.oauth_token_store", MemoryOAuthTokenStore()):
        with pytest.raises(ValueError, match="not connected"):
            get_access_token()


def test_google_api_edge_cases() -> None:
    settings = Settings(google_account_key="household", google_calendar_family_id="family-cal")
    with patch("shared.jarvis_common.google_api.settings", settings):
        with patch("shared.jarvis_common.google_api.oauth_status", return_value={"connected": True}):
            with patch("shared.jarvis_common.google_api.get_access_token", return_value="token"):
                with patch("shared.jarvis_common.google_api._list_events", return_value=[]):
                    assert calendar_read(_envelope(date="today"))["source"] == "google_calendar_api"
                with pytest.raises(ValueError, match="Unsupported calendar scope"):
                    calendar_write(
                        CommandEnvelope(
                            request_id="r8",
                            source="dashboard",
                            actor=Actor(user_id="u1", role="adult", device_id="d1"),
                            intent=Intent(name="add_event", parameters={"calendar_scope": "unknown"}),
                            context=Context(),
                        )
                    )
                with patch("shared.jarvis_common.google_api.httpx.Client") as client:
                    client.return_value.__enter__.return_value.get.return_value = MagicMock(
                        raise_for_status=lambda: None,
                        json=lambda: {"items": [{"title": "Shopping", "id": "list-2"}]},
                    )
                    client.return_value.__enter__.return_value.post.return_value = MagicMock(
                        raise_for_status=lambda: None,
                        json=lambda: {"id": "task-2"},
                    )
                    task = task_create(
                        CommandEnvelope(
                            request_id="r9",
                            source="dashboard",
                            actor=Actor(user_id="u1", role="adult", device_id="d1"),
                            intent=Intent(name="add_task", parameters={"title": "Eggs", "list": "shopping"}),
                            context=Context(),
                        )
                    )
                    assert task["task_id"] == "task-2"
                with patch("shared.jarvis_common.google_api.httpx.Client") as client:
                    client.return_value.__enter__.return_value.post.return_value = MagicMock(
                        raise_for_status=lambda: None,
                        json=lambda: ["bad"],
                    )
                    with pytest.raises(ValueError, match="Invalid Google API response"):
                        calendar_write(
                            CommandEnvelope(
                                request_id="r10",
                                source="dashboard",
                                actor=Actor(user_id="u1", role="adult", device_id="d1"),
                                intent=Intent(
                                    name="add_event",
                                    parameters={
                                        "title": "Party",
                                        "calendar_scope": "family_shared",
                                        "date": "today",
                                        "location": "Home",
                                        "notes": "Bring snacks",
                                    },
                                ),
                                context=Context(timezone="America/Chicago"),
                            )
                        )
                from shared.jarvis_common.google_api import _time_payload

                assert _time_payload("2026-07-11T09:00:00", "America/Chicago")["dateTime"].endswith(":00")
                assert _time_payload("2026-07-11T09:00:00-05:00", "America/Chicago")["dateTime"].endswith("-05:00")
                with patch("shared.jarvis_common.google_api.httpx.Client") as client:
                    client.return_value.__enter__.return_value.get.return_value = MagicMock(
                        raise_for_status=lambda: None,
                        json=lambda: {"items": []},
                    )
                    client.return_value.__enter__.return_value.post.return_value = MagicMock(
                        raise_for_status=lambda: None,
                        json=lambda: {"id": "new-list"},
                    )
                    created = task_create(
                        CommandEnvelope(
                            request_id="r11",
                            source="dashboard",
                            actor=Actor(user_id="u1", role="adult", device_id="d1"),
                            intent=Intent(name="add_task", parameters={"title": "Bread", "list": "household"}),
                            context=Context(),
                        )
                    )
                    assert created["task_id"] == "new-list"
                with patch("shared.jarvis_common.google_api.httpx.Client") as client:
                    client.return_value.__enter__.return_value.get.return_value = MagicMock(
                        raise_for_status=lambda: None,
                        json=lambda: ["bad"],
                    )
                    with pytest.raises(ValueError, match="Invalid Google API response"):
                        calendar_read(_envelope(date="2026-07-11"))
