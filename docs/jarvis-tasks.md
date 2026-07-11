# Jarvis Implementation Task Backlog

## 1) Planning Assumptions
- Docker-first deployment on Mac Mini M4.
- Public ingress exception only for Alexa webhook.
- Dashboard and admin usage remain VPN-only.
- MVP target: calendar + tasks/reminders + Alexa voice + secure approvals.

## 2) Phase 0 - Foundation
### T0.1 Repository and service skeletons
- Create service directories and contracts for core, ingress, scheduler, connectors, web.
- Define shared config strategy and environment layering.
- Acceptance: directories, baseline docs, and architecture map are stable.

### T0.2 Local infrastructure baseline
- Define Docker Compose stack with isolated networks and persistent volumes.
- Add health endpoints and startup dependency ordering.
- Acceptance: stack starts deterministically and all health checks pass.

### T0.3 Data model baseline
- Define schemas for users, roles, calendars, events, tasks, approvals, audit logs.
- Acceptance: schema migration plan exists and supports MVP workflows.

## 3) Phase 1 - Security and Access
### T1.1 Identity and role model
- Implement household identity model with adult/child/system roles.
- Acceptance: role checks are enforceable through centralized policy.

### T1.2 Sensitive action gating
- Implement `ALLOW | DENY | REQUIRE_CONFIRMATION` policy outcomes.
- Add PIN/app confirmation path for sensitive commands.
- Acceptance: calendar writes and equivalent actions require confirmation when configured.

### T1.3 Network hardening controls
- Restrict public exposure to Alexa ingress endpoint only.
- Enforce VPN-only access for dashboard/admin APIs.
- Acceptance: external scans show no unintended public endpoints.

## 4) Phase 1 - Core Product Capabilities
### T2.1 Alexa ingress integration
- Implement signature/timestamp verification and intent normalization.
- Map core intents (`what_today`, `add_event`, `add_task`, `set_timer`).
- Acceptance: Echo/Show commands execute and return safe voice responses.

### T2.2 Core command orchestration
- Implement command envelope parsing, policy evaluation, and execution routing.
- Acceptance: command outcomes are deterministic and audit-logged.

### T2.3 Google calendar and tasks connectors
- Implement OAuth token lifecycle and connector adapters.
- Support family shared + spouse personal calendars (read/write) and work calendar (read-only).
- Acceptance: events/tasks sync reliably with conflict and retry handling.

### T2.4 Scheduler and reminders
- Implement recurring reminder jobs and date-night monthly reminder.
- Acceptance: reminder triggers are accurate and recover after restart.

### T2.5 Dashboard (VPN-only)
- Build responsive views for timeline, pending approvals, and schedule/tasks.
- Acceptance: iPhone browser access over VPN is stable; approvals work end-to-end.

## 5) Phase 1 - Reliability and Operations
### T3.1 Audit and observability
- Implement structured logs, correlation IDs, and action audit stream.
- Acceptance: each request traces from ingress through side effects.

### T3.2 Backup and restore
- Implement backup procedure for state DB and required configs.
- Acceptance: restore drill passes and is documented.

### T3.3 Security validation
- Run hardening checklist from `jarvis-security.md`.
- Acceptance: ingress validation, auth controls, and log redaction verified.

## 6) Phase 2 - Priority Expansion
### T4.1 Email summarization pipeline
- Add selected mailbox ingestion and summarization.
- Acceptance: daily summaries generated with configurable cadence.

### T4.2 School-event extraction workflow
- Parse school emails for actionable events/reminders.
- Require human confirmation before calendar writes.
- Acceptance: extracted events are reviewable and acceptance rate is measurable.

## 7) Cross-Cutting Engineering Tasks
- Testing strategy:
  - Unit tests for policy and command routing
  - Integration tests for Alexa ingress and Google connectors
  - End-to-end tests for voice-to-calendar/task flows
- Performance benchmarking:
  - Compare Docker vs native model-serving latency
  - Decide on fallback if p95 exceeds threshold
- Documentation:
  - Ops runbooks
  - Onboarding/setup for household members
  - Recovery procedures

## 8) Prioritized Delivery Order
1. Security boundary + ingress hardening
2. Core command/policy engine
3. Google calendar/task integration
4. Alexa voice path
5. Dashboard approvals and timeline
6. Observability + backup/recovery validation
7. Phase 2 email and school automation

## 9) MVP Exit Criteria
- Echo/Show successfully handles key calendar/task voice commands.
- Sensitive actions require confirmation and are audit-traceable.
- Dashboard is usable over VPN from iPhone.
- Public exposure remains limited to Alexa ingress endpoint.
- Data backup/restore and token lifecycle are operationally documented.
