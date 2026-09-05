from __future__ import annotations

import argparse
from pathlib import Path

from shared.jarvis_common.config import settings
from shared.jarvis_common.ops.backup import BackupConfig, create_backup


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create a Jarvis state backup archive.")
    parser.add_argument("--output-dir", default="backups", type=Path)
    parser.add_argument("--compose-file", default="docker-compose.yml", type=Path)
    parser.add_argument("--env-file", default=".env", type=Path)
    parser.add_argument("--database-url", default="", help="Direct DATABASE_URL instead of docker compose pg_dump")
    args = parser.parse_args(argv)

    config = BackupConfig(
        backup_dir=args.output_dir,
        compose_file=args.compose_file,
        env_file=args.env_file,
    )
    result = create_backup(config, database_url=args.database_url or settings.database_url)
    print(result.archive_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
