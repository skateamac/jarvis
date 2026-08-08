from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

import psycopg.rows

from shared.jarvis_common.db.connection import db_connection


@dataclass
class OAuthTokenRecord:
    provider: str
    account_key: str
    access_token: str
    refresh_token: Optional[str]
    expires_at: Optional[datetime]
    scopes: List[str]
    metadata: Dict[str, Any]


class MemoryOAuthTokenStore:
    def __init__(self) -> None:
        self._records: Dict[tuple[str, str], OAuthTokenRecord] = {}

    def clear(self) -> None:
        self._records.clear()

    def upsert(self, record: OAuthTokenRecord) -> OAuthTokenRecord:
        self._records[(record.provider, record.account_key)] = record
        return record

    def get(self, provider: str, account_key: str) -> Optional[OAuthTokenRecord]:
        return self._records.get((provider, account_key))

    def list_connected(self, provider: str) -> List[OAuthTokenRecord]:
        return [record for (p, _), record in self._records.items() if p == provider]


class PostgresOAuthTokenStore:
    def clear(self) -> None:
        with db_connection() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM oauth_tokens")
            conn.commit()

    def upsert(self, record: OAuthTokenRecord) -> OAuthTokenRecord:
        token_id = str(uuid.uuid4())
        with db_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO oauth_tokens
                    (id, provider, account_key, access_token, refresh_token, expires_at, scopes, metadata)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                ON CONFLICT (provider, account_key) DO UPDATE SET
                    access_token = EXCLUDED.access_token,
                    refresh_token = EXCLUDED.refresh_token,
                    expires_at = EXCLUDED.expires_at,
                    scopes = EXCLUDED.scopes,
                    metadata = EXCLUDED.metadata,
                    updated_at = NOW()
                """,
                (
                    token_id,
                    record.provider,
                    record.account_key,
                    record.access_token,
                    record.refresh_token,
                    record.expires_at,
                    record.scopes,
                    json.dumps(record.metadata),
                ),
            )
            conn.commit()
        return record

    def get(self, provider: str, account_key: str) -> Optional[OAuthTokenRecord]:
        with db_connection() as conn:
            cur = conn.cursor(row_factory=psycopg.rows.dict_row)
            cur.execute(
                """
                SELECT provider, account_key, access_token, refresh_token, expires_at, scopes, metadata
                FROM oauth_tokens WHERE provider = %s AND account_key = %s
                """,
                (provider, account_key),
            )
            row = cur.fetchone()
        return _oauth_from_row(row) if row else None

    def list_connected(self, provider: str) -> List[OAuthTokenRecord]:
        with db_connection() as conn:
            cur = conn.cursor(row_factory=psycopg.rows.dict_row)
            cur.execute(
                """
                SELECT provider, account_key, access_token, refresh_token, expires_at, scopes, metadata
                FROM oauth_tokens WHERE provider = %s ORDER BY updated_at DESC
                """,
                (provider,),
            )
            rows = cur.fetchall()
        return [_oauth_from_row(row) for row in rows]


def _oauth_from_row(row: Dict[str, Any]) -> OAuthTokenRecord:
    metadata = row["metadata"]
    if isinstance(metadata, str):
        metadata = json.loads(metadata)
    return OAuthTokenRecord(
        provider=row["provider"],
        account_key=row["account_key"],
        access_token=row["access_token"],
        refresh_token=row["refresh_token"],
        expires_at=row["expires_at"],
        scopes=list(row["scopes"] or []),
        metadata=metadata,
    )


def create_oauth_token_store():
    from shared.jarvis_common.config import settings

    if settings.database_url:
        return PostgresOAuthTokenStore()
    return MemoryOAuthTokenStore()


oauth_token_store = create_oauth_token_store()
