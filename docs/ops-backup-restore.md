# Jarvis Backup and Restore

This runbook covers operational backup/restore for Jarvis state on the Mac Mini Docker stack.

## What gets backed up

Each backup archive (`.tar.gz`) contains:

- `postgres.sql` — full Postgres dump (approvals, audit events, OAuth tokens, scheduler jobs, etc.)
- `env.redacted` — `.env` key list with sensitive values redacted
- `manifest.json` — backup metadata and file inventory

Sensitive values (passwords, tokens, PINs, secrets) are never written in plaintext to backup artifacts.

## Prerequisites

- Docker Compose stack running with the `postgres` service healthy
- `.env` present at repo root (used for redacted snapshot only)
- Write access to `backups/` (gitignored)

## Create a backup

From the repo root:

```bash
.venv/bin/python scripts/jarvis_backup.py
```

Options:

- `--output-dir backups` — destination directory (default: `backups/`)
- `--compose-file docker-compose.yml`
- `--env-file .env`
- `--database-url postgresql://...` — skip Docker and dump via local `pg_dump`

Example output:

```text
backups/jarvis-backup-20260823T161500Z.tar.gz
```

Recommended cadence: nightly automated backup via cron or launchd, plus manual backup before upgrades.

## Restore drill

1. Stop application services (keep Postgres running):

```bash
docker compose stop core alexa-ingress web scheduler connectors-google policy
```

2. Restore from archive:

```bash
.venv/bin/python scripts/jarvis_restore.py backups/jarvis-backup-YYYYMMDDTHHMMSSZ.tar.gz
```

3. Restart services:

```bash
docker compose up -d
```

4. Verify:

- `curl http://localhost:8006/health`
- Dashboard timeline shows historical audit events
- Google OAuth status reflects restored tokens (`/internal/connectors/google/oauth/status` via internal network)

## Direct database URL restore

When running Postgres outside Compose:

```bash
.venv/bin/python scripts/jarvis_restore.py backups/jarvis-backup-....tar.gz \
  --database-url "$DATABASE_URL"
```

## Secrets not in backup

The redacted env snapshot is for inventory only. Store a separate encrypted copy of the real `.env` (or secret manager export) for full disaster recovery.

Minimum secrets to recover manually if lost:

- `POSTGRES_PASSWORD`
- `JARVIS_HOUSEHOLD_PIN`
- `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET`
- Google OAuth tokens (restored from DB if backup is recent)

## Correlation with observability

Structured logs include `correlation_id` (same as command `request_id` for voice/dashboard flows). After restore, audit timeline entries remain queryable by `request_id` for incident timelines.

## Automation sketch (cron)

```bash
0 3 * * * cd /path/to/jarvis && .venv/bin/python scripts/jarvis_backup.py >> /var/log/jarvis-backup.log 2>&1
```

Rotate or copy archives off-host (encrypted volume, NAS, object storage).
