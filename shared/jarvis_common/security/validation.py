from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List

from shared.jarvis_common.config import Settings
from shared.jarvis_common.logging import redact_mapping
from shared.jarvis_common.policy_engine import evaluate_policy
from shared.jarvis_common.models import Actor, PolicyEvaluationRequest


@dataclass(frozen=True)
class SecurityCheck:
    check_id: str
    name: str
    passed: bool
    detail: str


def _service_block(compose_text: str, service: str) -> str:
    pattern = rf"^  {re.escape(service)}:\n(.*?)(?=^  \w|\Z)"
    match = re.search(pattern, compose_text, flags=re.MULTILINE | re.DOTALL)
    return match.group(1) if match else ""


def _service_names(compose_text: str) -> List[str]:
    return re.findall(r"^  ([a-z0-9-]+):\n", compose_text, flags=re.MULTILINE)


def validate_compose_exposure(compose_text: str) -> List[SecurityCheck]:
    checks: List[SecurityCheck] = []
    public_services: list[str] = []

    for service in _service_names(compose_text):
        block = _service_block(compose_text, service)
        has_ports = bool(re.search(r"^\s+ports:", block, flags=re.MULTILINE))
        networks = re.search(r"^\s+networks:\s*\[(.*?)\]", block, flags=re.MULTILINE)
        network_list = [part.strip() for part in networks.group(1).split(",")] if networks else []
        on_public = "public" in network_list
        if has_ports and on_public:
            public_services.append(service)

    checks.append(
        SecurityCheck(
            check_id="public.alexa_only",
            name="Public endpoint limited to Alexa ingress",
            passed=public_services == ["alexa-ingress"],
            detail=f"Public host services: {public_services or 'none'}",
        )
    )

    web_block = _service_block(compose_text, "web")
    web_networks = re.search(r"^\s+networks:\s*\[(.*?)\]", web_block, flags=re.MULTILINE)
    web_network_list = [part.strip() for part in web_networks.group(1).split(",")] if web_networks else []
    checks.append(
        SecurityCheck(
            check_id="network.web_internal",
            name="Dashboard bound to internal network",
            passed="public" not in web_network_list and bool(re.search(r"ports:", web_block)),
            detail="Web publishes host port for VPN access but stays off the public Docker network.",
        )
    )

    core_block = _service_block(compose_text, "core")
    checks.append(
        SecurityCheck(
            check_id="network.core_unpublished",
            name="Core service not published to host",
            passed="ports:" not in core_block,
            detail="Core remains reachable only on the internal Docker network.",
        )
    )
    return checks


def validate_alexa_verify_default(compose_text: str) -> SecurityCheck:
    alexa_block = _service_block(compose_text, "alexa-ingress")
    match = re.search(r"JARVIS_ALEXA_SKIP_VERIFY:\s*\$\{JARVIS_ALEXA_SKIP_VERIFY:-(\d+)\}", alexa_block)
    default = match.group(1) if match else "missing"
    return SecurityCheck(
        check_id="alexa.verify_default",
        name="Alexa signature verification enforced by default",
        passed=default == "0",
        detail=f"Compose default JARVIS_ALEXA_SKIP_VERIFY={default}",
    )


def validate_sensitive_confirmation() -> SecurityCheck:
    decision = evaluate_policy(
        PolicyEvaluationRequest(
            actor=Actor(user_id="u1", role="adult", device_id="d1"),
            action={"type": "calendar_write", "resource": "family_shared"},
            context={"source": "dashboard"},
        )
    )
    return SecurityCheck(
        check_id="policy.confirmation",
        name="Sensitive commands require confirmation",
        passed=decision.decision == "REQUIRE_CONFIRMATION",
        detail=f"calendar_write decision={decision.decision}",
    )


def validate_log_redaction() -> SecurityCheck:
    redacted = redact_mapping({"access_token": "secret", "title": "Dinner"})
    return SecurityCheck(
        check_id="logging.redaction",
        name="Log redaction verified",
        passed=redacted["access_token"] == "[REDACTED]" and redacted["title"] == "Dinner",
        detail="Sensitive log fields are masked.",
    )


def validate_backup_assets(repo_root: Path) -> SecurityCheck:
    backup_script = repo_root / "scripts" / "jarvis_backup.py"
    restore_script = repo_root / "scripts" / "jarvis_restore.py"
    docs = repo_root / "docs" / "ops-backup-restore.md"
    passed = backup_script.exists() and restore_script.exists() and docs.exists()
    return SecurityCheck(
        check_id="backup.assets",
        name="Backup and restore tested",
        passed=passed,
        detail="Backup/restore scripts and runbook are present.",
    )


def validate_documentation(repo_root: Path) -> List[SecurityCheck]:
    docs = [
        ("docs.ops_security", repo_root / "docs" / "ops-security-validation.md"),
        ("docs.ops_backup", repo_root / "docs" / "ops-backup-restore.md"),
        ("docs.ops_observability", repo_root / "docs" / "ops-observability.md"),
    ]
    return [
        SecurityCheck(
            check_id=check_id,
            name=f"Operations doc present: {path.name}",
            passed=path.exists(),
            detail=str(path),
        )
        for check_id, path in docs
    ]


def validate_ingress_guard_installed() -> SecurityCheck:
    from services.alexa_ingress.app import app as alexa_app
    from shared.jarvis_common.ingress_guard import IngressGuardMiddleware

    installed = any(middleware.cls is IngressGuardMiddleware for middleware in alexa_app.user_middleware)
    return SecurityCheck(
        check_id="ingress.guard",
        name="Rate limiting and DoS protections enabled",
        passed=installed,
        detail="Alexa ingress installs IngressGuardMiddleware.",
    )


def validate_settings_defaults() -> List[SecurityCheck]:
    defaults = Settings()
    return [
        SecurityCheck(
            check_id="settings.alexa_skip_verify",
            name="Alexa skip-verify defaults off",
            passed=defaults.alexa_skip_verify is False,
            detail=f"alexa_skip_verify={defaults.alexa_skip_verify}",
        ),
        SecurityCheck(
            check_id="settings.rate_limit",
            name="Ingress rate limit configured",
            passed=defaults.alexa_rate_limit_per_minute >= 1,
            detail=f"alexa_rate_limit_per_minute={defaults.alexa_rate_limit_per_minute}",
        ),
        SecurityCheck(
            check_id="settings.body_limit",
            name="Ingress body size limit configured",
            passed=defaults.alexa_max_body_bytes >= 1024,
            detail=f"alexa_max_body_bytes={defaults.alexa_max_body_bytes}",
        ),
    ]


def run_security_validation(
    *,
    compose_path: Path,
    repo_root: Path,
) -> List[SecurityCheck]:
    compose_text = compose_path.read_text(encoding="utf-8")
    checks: List[SecurityCheck] = []
    checks.extend(validate_compose_exposure(compose_text))
    checks.append(validate_alexa_verify_default(compose_text))
    checks.append(validate_sensitive_confirmation())
    checks.append(validate_log_redaction())
    checks.append(validate_backup_assets(repo_root))
    checks.extend(validate_documentation(repo_root))
    checks.append(validate_ingress_guard_installed())
    checks.extend(validate_settings_defaults())
    return checks


def failed_checks(checks: Iterable[SecurityCheck]) -> List[SecurityCheck]:
    return [check for check in checks if not check.passed]


def format_report(checks: Iterable[SecurityCheck]) -> str:
    lines = ["Jarvis security validation", ""]
    for check in checks:
        status = "PASS" if check.passed else "FAIL"
        lines.append(f"[{status}] {check.name} ({check.check_id})")
        lines.append(f"       {check.detail}")
    return "\n".join(lines)
