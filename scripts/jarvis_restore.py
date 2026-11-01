from __future__ import annotations

import argparse
from pathlib import Path

from shared.jarvis_common.config import settings
from shared.jarvis_common.ops.backup import BackupConfig, restore_backup


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Restore Jarvis state from a backup archive.")
    parser.add_argument("archive", type=Path)
    parser.add_argument("--compose-file", default="docker-compose.yml", type=Path)
    parser.add_argument("--database-url", default="", help="Direct DATABASE_URL instead of docker compose psql")
    args = parser.parse_args(argv)

    config = BackupConfig(compose_file=args.compose_file)
    manifest = restore_backup(args.archive, config, database_url=args.database_url or settings.database_url)
    print(manifest.get("label", args.archive.name))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
