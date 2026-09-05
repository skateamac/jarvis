from __future__ import annotations

import json
import tarfile
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from scripts import jarvis_backup, jarvis_restore
from shared.jarvis_common.ops.backup import (
    BackupConfig,
    backup_paths,
    build_manifest,
    create_backup,
    docker_compose_prefix,
    dump_database,
    extract_backup,
    pg_dump_direct,
    pg_dump_via_compose,
    redact_env_file,
    restore_backup,
    restore_database,
    run_command,
    timestamp_label,
)


def test_timestamp_and_manifest() -> None:
    when = datetime(2026, 8, 23, 16, 15, tzinfo=UTC)
    assert timestamp_label(when) == "20260823T161500Z"
    manifest = build_manifest(label="jarvis-backup-test", files=["postgres.sql"], notes=["ok"])
    assert manifest["label"] == "jarvis-backup-test"
    assert manifest["files"] == ["postgres.sql"]


def test_redact_env_file(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text("POSTGRES_PASSWORD=secret\nPUBLIC_FLAG=1\n", encoding="utf-8")
    redacted = redact_env_file(env_path)
    assert "POSTGRES_PASSWORD=[REDACTED]" in redacted
    assert "PUBLIC_FLAG=1" in redacted
    assert redact_env_file(tmp_path / "missing.env") == ""


def test_backup_paths(tmp_path: Path) -> None:
    config = BackupConfig(backup_dir=tmp_path)
    root, archive = backup_paths(config, when=datetime(2026, 1, 2, 3, 4, tzinfo=UTC))
    assert root.name == "jarvis-backup-20260102T030400Z"
    assert archive.suffix == ".gz"


def test_create_and_restore_backup_roundtrip(tmp_path: Path) -> None:
    config = BackupConfig(backup_dir=tmp_path, env_file=tmp_path / ".env")
    config.env_file.write_text("POSTGRES_PASSWORD=secret\n", encoding="utf-8")

    with patch(
        "shared.jarvis_common.ops.backup.dump_database",
        return_value=b"SELECT 1;\nCREATE TABLE demo(id int);\n",
    ):
        result = create_backup(config, when=datetime(2026, 8, 23, 12, 0, tzinfo=UTC))
    assert result.archive_path.exists()
    assert "postgres.sql" in result.manifest["files"]

    with tarfile.open(result.archive_path, "r:gz") as archive:
        names = archive.getnames()
    assert any(name.endswith("postgres.sql") for name in names)
    assert any(name.endswith("manifest.json") for name in names)

    restore_calls: list[Path] = []

    def fake_restore_db(active: BackupConfig, sql_path: Path, *, database_url: str = "") -> None:
        restore_calls.append(sql_path)
        assert sql_path.read_bytes().startswith(b"SELECT 1")

    with patch("shared.jarvis_common.ops.backup.restore_database", side_effect=fake_restore_db):
        manifest = restore_backup(result.archive_path, config, work_dir=tmp_path / "restore-work")
    assert manifest["label"] == "jarvis-backup-20260823T120000Z"
    assert restore_calls


def test_extract_backup_and_missing_sql(tmp_path: Path) -> None:
    config = BackupConfig(backup_dir=tmp_path, env_file=tmp_path / ".env")
    config.env_file.write_text("A=1\n", encoding="utf-8")
    with patch("shared.jarvis_common.ops.backup.dump_database", return_value=b"SQL"):
        archive = create_backup(config).archive_path

    extracted = extract_backup(archive, tmp_path / "extract")
    assert (extracted / "postgres.sql").exists()

    bad_root = tmp_path / "bad-backup"
    bad_root.mkdir()
    (bad_root / "manifest.json").write_text("{}", encoding="utf-8")
    bad_archive = tmp_path / "bad.tar.gz"
    with tarfile.open(bad_archive, "w:gz") as tar:
        tar.add(bad_root, arcname=bad_root.name)

    with pytest.raises(FileNotFoundError, match="postgres.sql"):
        restore_backup(bad_archive, config, work_dir=tmp_path / "bad-restore")


def test_dump_and_restore_database_commands(tmp_path: Path) -> None:
    config = BackupConfig()
    with patch(
        "shared.jarvis_common.ops.backup.run_command",
        return_value=type("R", (), {"stdout": b"dump"})(),
    ) as run:
        assert dump_database(config, database_url="postgresql://local") == b"dump"
        assert pg_dump_direct("postgresql://local", config) == b"dump"
        run.assert_called()

    with patch(
        "shared.jarvis_common.ops.backup.run_command",
        return_value=type("R", (), {"stdout": b"compose-dump"})(),
    ):
        assert pg_dump_via_compose(config) == b"compose-dump"

    sql_path = tmp_path / "postgres.sql"
    sql_path.write_bytes(b"SQL")
    with patch("shared.jarvis_common.ops.backup.run_command") as run:
        restore_database(config, sql_path, database_url="postgresql://local")
        restore_database(config, sql_path, database_url="")
    assert run.call_count == 2

    assert docker_compose_prefix(config) == ["docker", "compose", "-f", "docker-compose.yml"]
    assert run_command(["echo", "ok"]).stdout


def test_cli_scripts(tmp_path: Path) -> None:
    archive = tmp_path / "jarvis-backup-test.tar.gz"
    with patch(
        "scripts.jarvis_backup.create_backup",
        return_value=type("R", (), {"archive_path": archive, "manifest": {}})(),
    ):
        assert jarvis_backup.main(["--output-dir", str(tmp_path)]) == 0

    with patch("scripts.jarvis_restore.restore_backup", return_value={"label": "jarvis-backup-test"}):
        assert jarvis_restore.main([str(archive)]) == 0

    with patch("scripts.jarvis_restore.restore_backup", return_value={"label": "x"}):
        assert jarvis_restore.main([str(archive), "--database-url", "postgresql://x"]) == 0


def test_cli_main_blocks() -> None:
    import runpy

    with patch("scripts.jarvis_backup.main", return_value=0):
        with pytest.raises(SystemExit):
            runpy.run_path("scripts/jarvis_backup.py", run_name="__main__")

    with patch("scripts.jarvis_restore.main", return_value=0):
        with pytest.raises(SystemExit):
            runpy.run_path("scripts/jarvis_restore.py", run_name="__main__")


def test_dump_database_via_compose_and_restore_without_manifest(tmp_path: Path) -> None:
    config = BackupConfig(backup_dir=tmp_path, env_file=tmp_path / ".env")
    config.env_file.write_text("A=1\n", encoding="utf-8")

    with patch("shared.jarvis_common.ops.backup.pg_dump_via_compose", return_value=b"SQL"):
        archive = create_backup(config).archive_path

    no_manifest_root = tmp_path / "no-manifest"
    no_manifest_root.mkdir()
    (no_manifest_root / "postgres.sql").write_bytes(b"SQL")
    bad_archive = tmp_path / "no-manifest.tar.gz"
    with tarfile.open(bad_archive, "w:gz") as tar:
        tar.add(no_manifest_root, arcname=no_manifest_root.name)

    with patch("shared.jarvis_common.ops.backup.restore_database") as restore_db:
        manifest = restore_backup(bad_archive, config, work_dir=tmp_path / "restore-run")
    assert manifest == {}
    restore_db.assert_called_once()

    work_dir = tmp_path / "existing-work"
    work_dir.mkdir()
    (work_dir / "stale.txt").write_text("old", encoding="utf-8")
    with patch("shared.jarvis_common.ops.backup.restore_database"):
        restore_backup(bad_archive, config, work_dir=work_dir)
    assert not work_dir.exists()
