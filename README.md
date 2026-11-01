# jarvis
local AI on a Mac Mini M4

## Health checks (host-published)

- Alexa ingress (public): http://localhost:8002/health
- Web dashboard (VPN/host): http://localhost:8006/health and http://localhost:8006/

Internal services (`core`, `policy`, `scheduler`, `connectors-google`, `postgres`, `redis`) are not published to the host; they stay on the Docker `internal` network.

## Live Alexa setup

See [docs/ops-alexa-setup.md](docs/ops-alexa-setup.md) for Google OAuth, public HTTPS, and Alexa skill configuration. Interaction model: [docs/alexa-interaction-model.json](docs/alexa-interaction-model.json).

## Operations

- Observability: [docs/ops-observability.md](docs/ops-observability.md)
- Backup/restore: [docs/ops-backup-restore.md](docs/ops-backup-restore.md)
- Security validation: [docs/ops-security-validation.md](docs/ops-security-validation.md)
- Alexa live setup: [docs/ops-alexa-setup.md](docs/ops-alexa-setup.md)

```bash
# Backup
.venv/bin/python scripts/jarvis_backup.py

# Restore
.venv/bin/python scripts/jarvis_restore.py backups/jarvis-backup-YYYYMMDDTHHMMSSZ.tar.gz

# Security checklist
.venv/bin/python scripts/jarvis_security_check.py
```
