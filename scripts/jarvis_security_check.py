from __future__ import annotations

import argparse
import sys
from pathlib import Path

from shared.jarvis_common.security.validation import failed_checks, format_report, run_security_validation


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run Jarvis hardening checklist validation.")
    parser.add_argument("--compose-file", default="docker-compose.yml", type=Path)
    parser.add_argument("--repo-root", default=".", type=Path)
    args = parser.parse_args(argv)

    repo_root = args.repo_root.resolve()
    compose_path = (repo_root / args.compose_file).resolve()
    checks = run_security_validation(compose_path=compose_path, repo_root=repo_root)
    print(format_report(checks))
    return 1 if failed_checks(checks) else 0


if __name__ == "__main__":
    raise SystemExit(main())
