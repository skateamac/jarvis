# Jarvis Security Validation

This runbook validates MVP hardening controls from [jarvis-sec.md](jarvis-sec.md).

## Automated checklist

From the repo root:

```bash
.venv/bin/python scripts/jarvis_security_check.py
```

Exit code `0` means all automated checks passed. The script verifies:

- Alexa ingress is the only service on the public Docker network
- Core is not published to the host
- Web/dashboard stays on the internal network (VPN/host access only)
- Alexa signature verification defaults to **on** in Compose
- Policy requires confirmation for calendar writes
- Log redaction masks sensitive fields
- Backup/restore scripts and ops docs exist
- Alexa ingress rate limiting and body-size guard are installed

Run this after deploy changes and before marking a release merge-ready.

## Manual VPN validation

Automated checks cannot prove Tailscale/WireGuard is configured. Quarterly, from outside the home network (cellular):

1. Confirm `http://<public-ip>:8006/` is **unreachable**
2. Confirm `http://<public-ip>:8002/health` is reachable (Alexa ingress only)
3. Connect via VPN and confirm dashboard loads at `http://<mac-mini-tailscale-ip>:8006/`

Document the date and result in your ops log.

## Ingress controls

Alexa ingress enforces:

| Control | Default | Env override |
|---------|---------|--------------|
| Rate limit | 60 POST `/alexa/webhook` requests / minute / client IP | `JARVIS_ALEXA_RATE_LIMIT_PER_MINUTE` |
| Max body size | 256 KiB | `JARVIS_ALEXA_MAX_BODY_BYTES` |

Responses:

- `429` — rate limit exceeded
- `413` — body too large

`/health` is excluded from rate limiting.

## Secret rotation procedure

Rotate on schedule (quarterly) or immediately after suspected compromise.

### 1. Household PIN (`JARVIS_HOUSEHOLD_PIN`)

1. Choose a new PIN and update `.env`
2. `docker compose up -d web core`
3. Confirm dashboard approval flow accepts the new PIN
4. Notify household members

### 2. Postgres password

1. Update `POSTGRES_PASSWORD` in `.env`
2. `docker compose exec postgres psql -U jarvis -d jarvis -c "ALTER USER jarvis PASSWORD 'new';"`
3. Restart dependent services: `docker compose up -d`
4. Run `scripts/jarvis_backup.py` after confirming health checks

### 3. Google OAuth client secret

1. Create/rotate credentials in Google Cloud Console
2. Update `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REDIRECT_URI` in `.env`
3. Restart `connectors-google`
4. Re-run OAuth connect flow via `/internal/connectors/google/oauth/start`
5. Revoke old client secret in Google Cloud Console

### 4. Google refresh tokens (compromise response)

1. Revoke household Google account app access in Google Account security settings
2. Delete affected rows from `oauth_tokens` or restore from last known-good backup
3. Re-authenticate through the connector OAuth flow

Record each rotation in the audit timeline (dashboard action or ops note).

## Dependency update policy

### Routine cadence

- **Weekly**: review Dependabot/GitHub security advisories for Python, Postgres, Redis, and base images
- **Monthly**: apply patch/minor updates to Docker base images (`postgres`, `redis`, `python:*-slim`)
- **Quarterly**: upgrade pinned Python packages in `pyproject.toml` / `uv.lock` after tests pass

### Update process

1. Update lockfile / image tags on a branch
2. Run ` .venv/bin/python -m pytest tests/ -q`
3. Run `scripts/jarvis_security_check.py`
4. Deploy to Mac Mini and smoke-test Alexa + dashboard approval flow
5. Keep previous backup archive until 24h stable

### Emergency CVE response

1. Identify affected component (app dependency vs container image)
2. Patch within 72 hours for critical network-facing CVEs on Alexa ingress or dashboard
3. Patch within 7 days for internal-only dependency CVEs with no known exploit path

## Hardening checklist status

See [jarvis-sec.md §11](jarvis-sec.md#11-hardening-checklist) for the canonical checklist. Automated validation covers the items marked as machine-verifiable; VPN posture and live restore drills remain manual.
