from __future__ import annotations

from pathlib import Path
from typing import List

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def migration_files() -> List[Path]:
    return sorted(MIGRATIONS_DIR.glob("*.sql"))


def applied_versions(cursor) -> set[str]:
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version TEXT PRIMARY KEY,
            applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    cursor.execute("SELECT version FROM schema_migrations")
    return {row[0] for row in cursor.fetchall()}


def apply_migrations(conn) -> List[str]:
    applied: List[str] = []
    with conn.cursor() as cursor:
        done = applied_versions(cursor)
        for path in migration_files():
            version = path.stem
            if version in done:
                continue
            cursor.execute(path.read_text(encoding="utf-8"))
            cursor.execute(
                "INSERT INTO schema_migrations (version) VALUES (%s)",
                (version,),
            )
            applied.append(version)
    conn.commit()
    return applied
