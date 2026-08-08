from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

import psycopg

from shared.jarvis_common.config import settings


@contextmanager
def db_connection(dsn: str | None = None) -> Iterator[psycopg.Connection]:
    conn = psycopg.connect(dsn or settings.database_url)
    try:
        yield conn
    finally:
        conn.close()
