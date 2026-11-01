from __future__ import annotations

import json
import os
import shutil
import subprocess
import tarfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from shared.jarvis_common.logging import redact_env_lines

DEFAULT_BACKUP_DIR = Path("backups")
DEFAULT_COMPOSE_FILE = "docker-compose.yml"
DEFAULT_POSTGRES_SERVICE = "postgres"
DEFAULT_ENV_FILE = ".env"


@dataclass
class BackupConfig:
    backup_dir: Path = DEFAULT_BACKUP_DIR
    compose_file: Path = DEFAULT_COMPOSE_FILE
    postgres_service: str = DEFAULT_POSTGRES_SERVICE
    env_file: Path = DEFAULT_ENV_FILE
    pg_dump_binary: str = "pg_dump"
    psql_binary: str = "psql"


@dataclass
class BackupResult:
    archive_path: Path
    manifest: Dict[str, Any]


def timestamp_label(when: Optional[datetime] = None) -> str:
    current = when or datetime.now(UTC)
    return current.strftime("%Y%m%dT%H%M%SZ")


def backup_paths(config: BackupConfig, when: Optional[datetime] = None) -> tuple[Path, Path]:
    label = timestamp_label(when)
    root = config.backup_dir / f"jarvis-backup-{label}"
    return root, root.with_suffix(".tar.gz")


def redact_env_file(path: Path) -> str:
    if not path.exists():
        return ""
    return "\n".join(redact_env_lines(path.read_text(encoding="utf-8").splitlines())) + "\n"


def build_manifest(*, label: str, files: Iterable[str], notes: Optional[List[str]] = None) -> Dict[str, Any]:
    return {
        "created_at": datetime.now(UTC).isoformat(),
        "label": label,
        "files": list(files),
        "notes": notes or [],
    }


def run_command(command: List[str], *, input_data: Optional[bytes] = None) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(command, input=input_data, capture_output=True, check=True)


def docker_compose_prefix(config: BackupConfig) -> List[str]:
    return ["docker", "compose", "-f", str(config.compose_file)]


def pg_dump_via_compose(config: BackupConfig) -> bytes:
    command = [
        *docker_compose_prefix(config),
        "exec",
        "-T",
        config.postgres_service,
        "pg_dump",
        "-U",
        os.getenv("POSTGRES_USER", "jarvis"),
        "-d",
        os.getenv("POSTGRES_DB", "jarvis"),
        "--format=plain",
        "--no-owner",
        "--no-privileges",
    ]
    return run_command(command).stdout


def pg_dump_direct(database_url: str, config: BackupConfig) -> bytes:
    return run_command([config.pg_dump_binary, database_url, "--format=plain", "--no-owner", "--no-privileges"]).stdout


def dump_database(config: BackupConfig, *, database_url: str = "") -> bytes:
    if database_url:
        return pg_dump_direct(database_url, config)
    return pg_dump_via_compose(config)


def create_backup(
    config: Optional[BackupConfig] = None,
    *,
    database_url: str = "",
    when: Optional[datetime] = None,
) -> BackupResult:
    active = config or BackupConfig()
    root, archive_path = backup_paths(active, when=when)
    root.mkdir(parents=True, exist_ok=True)

    sql_path = root / "postgres.sql"
    sql_path.write_bytes(dump_database(active, database_url=database_url))

    env_snapshot = root / "env.redacted"
    env_snapshot.write_text(redact_env_file(active.env_file), encoding="utf-8")

    manifest = build_manifest(
        label=root.name,
        files=["postgres.sql", "env.redacted"],
        notes=[
            "env.redacted contains key names only with sensitive values redacted.",
            "Store an encrypted copy of the real .env separately for disaster recovery.",
        ],
    )
    (root / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    with tarfile.open(archive_path, "w:gz") as archive:
        archive.add(root, arcname=root.name)

    shutil.rmtree(root)
    return BackupResult(archive_path=archive_path, manifest=manifest)


def extract_backup(archive_path: Path, destination: Path) -> Path:
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive_path, "r:gz") as archive:
        archive.extractall(destination, filter="data")
    extracted = next(destination.iterdir())
    return extracted


def restore_database(config: BackupConfig, sql_path: Path, *, database_url: str = "") -> None:
    sql_bytes = sql_path.read_bytes()
    if database_url:
        run_command([config.psql_binary, database_url], input_data=sql_bytes)
        return
    command = [
        *docker_compose_prefix(config),
        "exec",
        "-T",
        config.postgres_service,
        "psql",
        "-U",
        os.getenv("POSTGRES_USER", "jarvis"),
        "-d",
        os.getenv("POSTGRES_DB", "jarvis"),
    ]
    run_command(command, input_data=sql_bytes)


def restore_backup(
    archive_path: Path,
    config: Optional[BackupConfig] = None,
    *,
    database_url: str = "",
    work_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    active = config or BackupConfig()
    workspace = work_dir or active.backup_dir / "restore-work"
    if workspace.exists():
        shutil.rmtree(workspace)
    extracted = extract_backup(archive_path, workspace)
    sql_path = extracted / "postgres.sql"
    if not sql_path.exists():
        raise FileNotFoundError(f"Missing postgres.sql in backup {archive_path}")
    restore_database(active, sql_path, database_url=database_url)
    manifest_path = extracted / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    shutil.rmtree(workspace)
    return manifest
