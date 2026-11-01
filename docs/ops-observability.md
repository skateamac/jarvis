# Jarvis Observability

Jarvis services emit structured JSON logs with correlation IDs so requests trace from ingress through side effects.

## Correlation IDs

- HTTP header: `X-Correlation-ID` (alias: `X-Request-ID`)
- Command flows use the command envelope `request_id` as the canonical correlation ID
- The header is returned on every HTTP response for client-side tracing

Flow example (Alexa → core → connector):

1. Alexa ingress binds correlation from Alexa `requestId`
2. Core orchestrator logs `command.received`, `policy.decision`, `command.executed`
3. HTTP clients propagate `X-Correlation-ID` to connectors/scheduler
4. Connector logs `connector.calendar_write` with the same ID
5. Audit store appends `audit.appended` log + durable timeline row

## Log format

Each line is JSON:

```json
{
  "timestamp": "2026-08-23T16:15:00+00:00",
  "level": "INFO",
  "logger": "jarvis.orchestrator",
  "message": "policy.decision",
  "service": "jarvis-core",
  "correlation_id": "req-abc-123",
  "event": "policy.decision",
  "request_id": "req-abc-123",
  "decision": "REQUIRE_CONFIRMATION"
}
```

## Redaction

Log and backup redaction masks values for keys matching:

`password`, `secret`, `token`, `pin`, `authorization`, `api_key`, `refresh`, `access`, `credential`

## Key log events

| Event | Service | When |
|-------|---------|------|
| `request.start` / `request.complete` | all HTTP services | Every request |
| `command.received` | core | Command envelope accepted |
| `policy.decision` | core | Policy evaluation result |
| `command.executed` | core | Successful command |
| `audit.appended` | audit store | Timeline row written |
| `connector.*` | connectors-google | Calendar/task side effect |
| `scheduler.timer_scheduled` | scheduler | Timer job created |
| `scheduler.tick` | scheduler | Due jobs processed |

## Querying logs (Docker)

```bash
docker compose logs -f core alexa-ingress web | rg '"correlation_id":"YOUR-ID"'
```

## Audit timeline API

VPN dashboard and API consumers can fetch the durable audit stream:

```bash
curl "http://localhost:8006/v1/timeline?limit=50"
```

Each entry includes `request_id` for correlation with container logs.
