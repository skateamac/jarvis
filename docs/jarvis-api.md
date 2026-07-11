# Jarvis API Contracts (MVP)

## 1) API Design Principles
- Internal canonical API uses JSON over HTTPS.
- Every request carries `request_id` and `source`.
- Sensitive actions must return explicit confirmation requirements.
- APIs are idempotent where side effects are possible.

## 2) Service API Surface
### 2.1 External/Public
- `POST /alexa/webhook` (public, Alexa-only)

### 2.2 Private (VPN-only)
- `POST /v1/commands`
- `GET /v1/timeline`
- `GET /v1/approvals/pending`
- `POST /v1/approvals/{approval_id}/confirm`
- `POST /v1/approvals/{approval_id}/reject`
- `GET /v1/calendar/day`
- `POST /v1/calendar/events`
- `POST /v1/tasks`

### 2.3 Internal
- `POST /internal/policy/evaluate`
- `POST /internal/connectors/google/calendar/read`
- `POST /internal/connectors/google/calendar/write`
- `POST /internal/connectors/google/tasks/create`
- `POST /internal/scheduler/jobs`

## 3) Canonical Command Envelope
```json
{
  "request_id": "uuid",
  "source": "alexa|dashboard|scheduler",
  "actor": {
    "user_id": "string",
    "role": "adult|child|system",
    "device_id": "string"
  },
  "intent": {
    "name": "add_event|what_today|add_task|set_timer",
    "parameters": {}
  },
  "context": {
    "timezone": "America/Chicago",
    "locale": "en-US"
  }
}
```

## 4) Public Endpoint Contract
### 4.1 `POST /alexa/webhook`
Purpose: receive signed Alexa skill requests and return speech response payloads.

Request requirements:
- Must include valid Alexa signature headers.
- Must pass timestamp freshness checks.
- Body schema must match accepted Alexa interaction model.

Response shape (normalized):
```json
{
  "version": "1.0",
  "response": {
    "outputSpeech": {
      "type": "PlainText",
      "text": "Your reminder has been created."
    },
    "shouldEndSession": false
  }
}
```

Failure modes:
- `401`: signature invalid
- `400`: malformed request
- `429`: rate limit
- `500`: internal fault (generic voice-safe message)

## 5) Command API (VPN-only)
### 5.1 `POST /v1/commands`
Accepts canonical command envelope and returns execution or confirmation state.

Response:
```json
{
  "request_id": "uuid",
  "status": "executed|requires_confirmation|denied|failed",
  "message": "string",
  "approval_id": "uuid|null",
  "result": {}
}
```

## 6) Approval Workflow API
### 6.1 `GET /v1/approvals/pending`
Returns pending sensitive actions for authenticated household user.

### 6.2 `POST /v1/approvals/{approval_id}/confirm`
Request:
```json
{
  "confirmation_type": "app|pin",
  "confirmation_value": "opaque"
}
```

Response:
```json
{
  "approval_id": "uuid",
  "status": "approved|rejected|expired",
  "executed": true
}
```

## 7) Calendar API Contracts
### 7.1 `GET /v1/calendar/day?date=YYYY-MM-DD`
Returns merged day view for authorized calendars.

### 7.2 `POST /v1/calendar/events`
Request:
```json
{
  "calendar_scope": "family_shared|spouse_personal",
  "title": "Date night",
  "start_time": "2026-07-10T19:00:00-05:00",
  "end_time": "2026-07-10T21:00:00-05:00",
  "location": "Austin, TX",
  "notes": "Reservation at 7 PM"
}
```

Behavior:
- Work calendar writes are rejected by policy.
- Duplicate protection via idempotency key supported.

## 8) Task API Contracts
### 8.1 `POST /v1/tasks`
Request:
```json
{
  "list": "household|shopping|personal",
  "title": "Buy dog food",
  "due_date": "2026-07-01",
  "priority": "low|normal|high"
}
```

Response:
```json
{
  "task_id": "uuid",
  "status": "created"
}
```

## 9) Policy Evaluation Contract (Internal)
### 9.1 `POST /internal/policy/evaluate`
Request:
```json
{
  "actor": {
    "user_id": "string",
    "role": "adult|child|system"
  },
  "action": {
    "type": "calendar_write|task_create|calendar_read",
    "resource": "string"
  },
  "context": {}
}
```

Response:
```json
{
  "decision": "ALLOW|DENY|REQUIRE_CONFIRMATION",
  "reason": "string",
  "policy_version": "string"
}
```

## 10) Error Contract
All private APIs return standard error envelope:
```json
{
  "error": {
    "code": "string",
    "message": "string",
    "request_id": "uuid"
  }
}
```

Common error codes:
- `AUTH_REQUIRED`
- `AUTH_FORBIDDEN`
- `POLICY_CONFIRMATION_REQUIRED`
- `VALIDATION_ERROR`
- `RATE_LIMITED`
- `INTEGRATION_UNAVAILABLE`

## 11) Idempotency and Retries
- Write endpoints accept `Idempotency-Key` header.
- Scheduler and connector retries use exponential backoff with jitter.
- Duplicate side-effect creation must be prevented by request fingerprinting.

## 12) Versioning
- External/private APIs versioned via URI (`/v1/...`).
- Breaking changes require new version namespace.
- Alexa intent model changes require compatibility mapping during rollout.
