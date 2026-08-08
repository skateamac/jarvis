from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Iterator
from unittest.mock import MagicMock, patch

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.x509 import CertificateBuilder, Name, NameAttribute, SubjectAlternativeName
from cryptography.x509.oid import NameOID
from fastapi.testclient import TestClient

from services.connectors_google.app import app as connectors_app
from services.core.app import app as core_app
from shared.jarvis_common.alexa_verify import (
    fetch_cert_pem,
    validate_cert_url,
    verify_alexa_signature_headers,
    verify_signature,
)
from shared.jarvis_common.config import Settings
from shared.jarvis_common.db.connection import db_connection
from shared.jarvis_common.db.migrate import apply_migrations, migration_files
from shared.jarvis_common.db.oauth_store import (
    MemoryOAuthTokenStore,
    OAuthTokenRecord,
    PostgresOAuthTokenStore,
    _oauth_from_row,
    create_oauth_token_store,
)
from shared.jarvis_common.db.postgres_stores import (
    PostgresApprovalStore,
    PostgresAuditStore,
    _approval_from_row,
    _audit_from_row,
)
from shared.jarvis_common.google_oauth import (
    _post_token,
    complete_oauth,
    oauth_status,
    refresh_oauth_token,
    start_oauth,
)
from shared.jarvis_common.models import Actor, CommandEnvelope, Context, Intent
from shared.jarvis_common.stores import create_stores

client_connectors = TestClient(connectors_app)
client_core = TestClient(core_app)


class FakeCursor:
    def __init__(self, conn: "FakeConn") -> None:
        self._conn = conn
        self.row_factory = None

    def execute(self, sql: str, params: Any = None) -> None:
        self._conn.calls.append((sql, params))

    def fetchone(self) -> Any:
        return self._conn.fetchone_queue.pop(0) if self._conn.fetchone_queue else None

    def fetchall(self) -> list[Any]:
        return self._conn.fetchall_queue.pop(0) if self._conn.fetchall_queue else []

    def __enter__(self) -> "FakeCursor":
        return self

    def __exit__(self, *args: object) -> bool:
        return False


class FakeConn:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []
        self.fetchone_queue: list[Any] = []
        self.fetchall_queue: list[list[Any]] = []

    def cursor(self, row_factory: Any = None) -> FakeCursor:
        cur = FakeCursor(self)
        cur.row_factory = row_factory
        return cur

    def commit(self) -> None:
        return None

    def close(self) -> None:
        return None


@contextmanager
def fake_db() -> Iterator[FakeConn]:
    conn = FakeConn()

    @contextmanager
    def provide(_dsn: str | None = None) -> Iterator[FakeConn]:
        yield conn

    with patch("shared.jarvis_common.db.postgres_stores.db_connection", provide):
        with patch("shared.jarvis_common.db.oauth_store.db_connection", provide):
            yield conn


def _signed_body() -> tuple[bytes, str, bytes]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    cert = (
        CertificateBuilder()
        .subject_name(Name([NameAttribute(NameOID.COMMON_NAME, "alexa")]))
        .issuer_name(Name([NameAttribute(NameOID.COMMON_NAME, "alexa")]))
        .public_key(key.public_key())
        .serial_number(1)
        .not_valid_before(datetime.now(UTC) - timedelta(days=1))
        .not_valid_after(datetime.now(UTC) + timedelta(days=1))
        .add_extension(SubjectAlternativeName([]), critical=False)
        .sign(key, hashes.SHA256())
    )
    pem = cert.public_bytes(serialization.Encoding.PEM)
    body = b'{"request":{"type":"IntentRequest"}}'
    import base64

    signature = base64.b64encode(key.sign(body, padding.PKCS1v15(), hashes.SHA1())).decode()
    return pem, signature, body


def _envelope() -> CommandEnvelope:
    return CommandEnvelope(
        request_id="r1",
        source="dashboard",
        actor=Actor(user_id="u1", role="adult", device_id="d1"),
        intent=Intent(name="add_event", parameters={"title": "Dinner"}),
        context=Context(),
    )


def test_migrations_and_connection() -> None:
    assert migration_files()
    conn = FakeConn()
    conn.fetchall_queue = [[], []]
    sql_path = Path("001.sql")
    with patch("shared.jarvis_common.db.migrate.migration_files", return_value=[sql_path]):
        with patch.object(Path, "read_text", return_value="SELECT 1;"):
            assert apply_migrations(conn) == ["001"]
    with patch("shared.jarvis_common.db.connection.psycopg.connect", return_value=FakeConn()):
        with db_connection("postgresql://example") as c:
            assert isinstance(c, FakeConn)


def test_postgres_stores() -> None:
    envelope = _envelope()
    row = {
        "approval_id": "a1",
        "request_id": "r1",
        "envelope": envelope.model_dump(),
        "status": "pending",
        "created_at": datetime.now(UTC),
    }
    with fake_db() as conn:
        approvals = PostgresApprovalStore()
        approvals.clear()
        created = approvals.create(envelope)
        conn.fetchone_queue = [row]
        assert approvals.get(created.approval_id) is not None
        conn.fetchall_queue = [[row]]
        assert approvals.list_pending()
        conn.fetchone_queue = [row]
        confirmed = approvals.confirm(created.approval_id)
        assert confirmed is not None and confirmed.status == "approved"
        rejected_row = {**row, "status": "pending", "approval_id": "a2"}
        conn.fetchone_queue = [rejected_row]
        rejected = approvals.reject("a2")
        assert rejected is not None and rejected.status == "rejected"
        conn.fetchone_queue = [None]
        assert approvals.confirm("missing") is None

        audit = PostgresAuditStore()
        audit.clear()
        event = audit.append(
            request_id="r1",
            source="dashboard",
            actor_id="u1",
            action="add_event",
            outcome="executed",
            detail={"ok": True},
        )
        audit_row = {
            "event_id": event.event_id,
            "request_id": "r1",
            "source": "dashboard",
            "actor_id": "u1",
            "action": "add_event",
            "outcome": "executed",
            "detail": {"ok": True},
            "created_at": event.created_at,
        }
        conn.fetchall_queue = [[audit_row]]
        assert audit.list_events()[0].event_id == event.event_id


def test_oauth_stores_and_factory() -> None:
    record = OAuthTokenRecord(
        provider="google",
        account_key="household",
        access_token="a",
        refresh_token="r",
        expires_at=None,
        scopes=["calendar"],
        metadata={"token_type": "Bearer"},
    )
    mem = MemoryOAuthTokenStore()
    mem.upsert(record)
    assert mem.get("google", "household") == record
    assert mem.list_connected("google")
    with fake_db() as conn:
        pg = PostgresOAuthTokenStore()
        pg.clear()
        pg.upsert(record)
        oauth_row = {
            "provider": "google",
            "account_key": "household",
            "access_token": "a",
            "refresh_token": "r",
            "expires_at": None,
            "scopes": ["calendar"],
            "metadata": {"token_type": "Bearer"},
        }
        conn.fetchone_queue = [oauth_row]
        assert pg.get("google", "household") is not None
        conn.fetchall_queue = [[oauth_row]]
        assert pg.list_connected("google")
    with patch("shared.jarvis_common.config.settings", Settings(database_url="postgresql://x")):
        approval, _audit = create_stores()
        assert approval.__class__.__name__ == "PostgresApprovalStore"
    with patch("shared.jarvis_common.config.settings", Settings(database_url="postgresql://x")):
        assert create_oauth_token_store().__class__.__name__ == "PostgresOAuthTokenStore"


def test_row_helpers_string_payloads() -> None:
    envelope = _envelope()
    approval = _approval_from_row(
        {
            "approval_id": "a1",
            "request_id": "r1",
            "envelope": json.dumps(envelope.model_dump()),
            "status": "pending",
            "created_at": datetime.now(UTC),
        }
    )
    assert approval.envelope.intent.name == "add_event"
    audit = _audit_from_row(
        {
            "event_id": "e1",
            "request_id": "r1",
            "source": "dashboard",
            "actor_id": "u1",
            "action": "add_event",
            "outcome": "executed",
            "detail": json.dumps({"ok": True}),
            "created_at": datetime.now(UTC),
        }
    )
    assert audit.detail["ok"] is True
    oauth = _oauth_from_row(
        {
            "provider": "google",
            "account_key": "household",
            "access_token": "tok",
            "refresh_token": "ref",
            "expires_at": None,
            "scopes": ["calendar"],
            "metadata": json.dumps({"token_type": "Bearer"}),
        }
    )
    assert oauth.access_token == "tok"


def test_alexa_verify() -> None:
    validate_cert_url("https://s3.amazonaws.com/echo.api/cert.pem")
    with pytest.raises(ValueError, match="Invalid Alexa certificate URL"):
        validate_cert_url("http://s3.amazonaws.com/echo.api/cert.pem")
    with pytest.raises(ValueError, match="Invalid Alexa certificate URL"):
        validate_cert_url("https://evil.example/cert.pem")
    pem, signature, body = _signed_body()
    verify_signature(pem, signature, body)
    with pytest.raises(ValueError, match="Invalid Alexa request signature"):
        verify_signature(pem, signature, b"other")
    with patch("shared.jarvis_common.alexa_verify.httpx.Client") as mock_client:
        mock_client.return_value.__enter__.return_value.get.return_value = MagicMock(
            text=pem.decode(),
            raise_for_status=lambda: None,
        )
        assert fetch_cert_pem("https://s3.amazonaws.com/echo.api/cert.pem").startswith(b"-----BEGIN")
    with patch("shared.jarvis_common.alexa_verify.settings", Settings(alexa_skip_verify=True)):
        verify_alexa_signature_headers({}, b"")
    with patch("shared.jarvis_common.alexa_verify.fetch_cert_pem", return_value=pem):
        verify_alexa_signature_headers(
            {
                "signature": signature,
                "signaturecertchainurl": "https://s3.amazonaws.com/echo.api/cert.pem",
            },
            body,
        )


def test_google_oauth_and_connectors() -> None:
    settings = Settings(
        google_client_id="cid",
        google_client_secret="secret",
        google_redirect_uri="http://localhost/callback",
    )
    with patch("shared.jarvis_common.google_oauth.settings", settings):
        assert "accounts.google.com" in start_oauth()["auth_url"]
        store = MemoryOAuthTokenStore()
        with patch("shared.jarvis_common.google_oauth.oauth_token_store", store):
            with patch(
                "shared.jarvis_common.google_oauth._post_token",
                return_value={
                    "access_token": "access",
                    "refresh_token": "refresh",
                    "expires_in": 3600,
                    "scope": "calendar",
                },
            ):
                assert complete_oauth("code", "state:household").access_token == "access"
                assert oauth_status()["connected"] is True
                assert refresh_oauth_token().refresh_token == "refresh"
    with patch("shared.jarvis_common.google_oauth.settings", Settings()):
        with pytest.raises(ValueError, match="not configured"):
            start_oauth()
    with patch(
        "shared.jarvis_common.google_oauth.httpx.Client",
        return_value=MagicMock(
            __enter__=lambda s: MagicMock(
                post=MagicMock(
                    return_value=MagicMock(
                        raise_for_status=lambda: None,
                        json=lambda: {"access_token": "a", "expires_in": 10},
                    )
                )
            ),
            __exit__=lambda *a: None,
        ),
    ):
        assert _post_token({"grant_type": "authorization_code"})["access_token"] == "a"

    settings = Settings(
        google_client_id="cid",
        google_client_secret="secret",
        google_redirect_uri="http://localhost/callback",
    )
    with patch("shared.jarvis_common.google_oauth.settings", settings):
        assert client_connectors.get("/internal/connectors/google/oauth/start").status_code == 200
        with patch("services.connectors_google.app.complete_oauth", side_effect=ValueError("bad")):
            assert (
                client_connectors.get(
                    "/internal/connectors/google/oauth/callback", params={"code": "c", "state": "s"}
                ).status_code
                == 400
            )
        with patch("services.connectors_google.app.complete_oauth", side_effect=RuntimeError("fail")):
            assert (
                client_connectors.get(
                    "/internal/connectors/google/oauth/callback", params={"code": "c", "state": "s"}
                ).status_code
                == 502
            )
        with patch("services.connectors_google.app.refresh_oauth_token", side_effect=RuntimeError("fail")):
            assert client_connectors.post("/internal/connectors/google/oauth/refresh").status_code == 502
        assert client_connectors.get("/internal/connectors/google/oauth/status").status_code == 200
    with patch("services.connectors_google.app.start_oauth", side_effect=ValueError("missing")):
        assert client_connectors.get("/internal/connectors/google/oauth/start").status_code == 503

    with patch("services.connectors_google.app.settings", Settings(database_url="postgresql://x")):
        with patch("services.connectors_google.app.db_connection") as db_mock:
            db_mock.return_value.__enter__.return_value = FakeConn()
            db_mock.return_value.__exit__.return_value = None
            with patch("services.connectors_google.app.apply_migrations") as migrate:
                with TestClient(connectors_app) as startup_client:
                    startup_client.get("/health")
                migrate.assert_called_once()
    with patch("services.core.app.settings", Settings(database_url="postgresql://x")):
        with patch("services.core.app.db_connection") as db_mock:
            db_mock.return_value.__enter__.return_value = FakeConn()
            db_mock.return_value.__exit__.return_value = None
            with patch("services.core.app.apply_migrations") as migrate:
                with TestClient(core_app) as startup_client:
                    payload = startup_client.get("/health").json()
                assert payload["database"] is True
                migrate.assert_called_once()


def test_remaining_db_coverage() -> None:
    conn = FakeConn()
    conn.fetchall_queue = [[("001_initial",)]]
    with patch("shared.jarvis_common.db.migrate.migration_files", return_value=[]):
        assert apply_migrations(conn) == []

    with pytest.raises(ValueError, match="Invalid Alexa certificate URL"):
        validate_cert_url("https://s3.amazonaws.com/other/cert.pem")
    pem, signature, body = _signed_body()
    with patch("shared.jarvis_common.alexa_verify.settings", Settings(alexa_skip_verify=False)):
        with pytest.raises(ValueError, match="Missing Alexa signature headers"):
            verify_alexa_signature_headers({}, body)

    settings = Settings(
        google_client_id="cid",
        google_client_secret="secret",
        google_redirect_uri="http://localhost/callback",
    )
    store = MemoryOAuthTokenStore()
    with patch("shared.jarvis_common.google_oauth.settings", settings):
        with patch("shared.jarvis_common.google_oauth.oauth_token_store", store):
            with patch(
                "shared.jarvis_common.google_oauth._post_token",
                return_value={"access_token": "access", "expires_in": 3600, "scope": "calendar"},
            ):
                assert complete_oauth("code", "state").account_key == "household"
            with pytest.raises(ValueError, match="No refresh token"):
                refresh_oauth_token()
    with pytest.raises(ValueError, match="Invalid token response"):
        with patch(
            "shared.jarvis_common.google_oauth.httpx.Client",
            return_value=MagicMock(
                __enter__=lambda s: MagicMock(
                    post=MagicMock(
                        return_value=MagicMock(
                            raise_for_status=lambda: None,
                            json=lambda: ["bad"],
                        )
                    )
                ),
                __exit__=lambda *a: None,
            ),
        ):
            _post_token({"grant_type": "authorization_code"})

    with patch("shared.jarvis_common.config.settings", Settings(database_url="postgresql://x")):
        approval, audit = create_stores()
        assert approval.__class__.__name__ == "PostgresApprovalStore"
        assert audit.__class__.__name__ == "PostgresAuditStore"

    with patch("services.connectors_google.app.refresh_oauth_token", side_effect=ValueError("missing")):
        assert client_connectors.post("/internal/connectors/google/oauth/refresh").status_code == 404

    record = OAuthTokenRecord("google", "household", "a", "r", None, [], {})
    with patch("services.connectors_google.app.complete_oauth", return_value=record):
        assert (
            client_connectors.get(
                "/internal/connectors/google/oauth/callback", params={"code": "c", "state": "s"}
            ).json()["status"]
            == "connected"
        )
    with patch("services.connectors_google.app.refresh_oauth_token", return_value=record):
        assert client_connectors.post("/internal/connectors/google/oauth/refresh").json()["status"] == "refreshed"
    with patch("services.connectors_google.app.google_task_create", side_effect=ValueError("bad task")):
        bad_task = client_connectors.post(
            "/internal/connectors/google/tasks/create",
            json={
                "request_id": "r-task",
                "source": "dashboard",
                "actor": {"user_id": "u1", "role": "adult", "device_id": "d1"},
                "intent": {"name": "add_task", "parameters": {"title": "X"}},
                "context": {"timezone": "America/Chicago", "locale": "en-US"},
            },
        )
        assert bad_task.status_code == 400

    with patch("shared.jarvis_common.google_oauth.settings", Settings()):
        with pytest.raises(ValueError, match="not configured"):
            complete_oauth("code", "state")

    conn = FakeConn()
    conn.fetchall_queue = [[("001_initial",)]]
    sql_path = Path("001_initial.sql")
    with patch("shared.jarvis_common.db.migrate.migration_files", return_value=[sql_path]):
        assert apply_migrations(conn) == []

    with patch("shared.jarvis_common.alexa_verify.settings", Settings(alexa_skip_verify=False)):
        with pytest.raises(ValueError, match="Empty Alexa request body"):
            verify_alexa_signature_headers(
                {
                    "signature": "s",
                    "signaturecertchainurl": "https://s3.amazonaws.com/echo.api/x.pem",
                },
                b"",
            )
        with patch("shared.jarvis_common.alexa_verify.fetch_cert_pem", return_value=pem):
            verify_alexa_signature_headers(
                {
                    "signature": signature,
                    "signaturecertchainurl": "https://s3.amazonaws.com/echo.api/x.pem",
                },
                body,
            )
