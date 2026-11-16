# Production Considerations

The educational version runs on one laptop with a simulated clock. Moving toward production means addressing each area below.

**Authentication & API permissions.** OAuth apps with least-privilege scopes per system (see `integrating-real-systems.md`). Separate the identity that reads from the identity that writes. Rotate tokens and store them in a secret manager.

**Event-driven integrations.** Replace `simulate-reply` with webhook handlers (Slack Events API, Monday.com webhooks, Gmail Pub/Sub). Ack fast (Slack: 3 s), enqueue, process asynchronously, and dedupe on event IDs.

**Reliable scheduling.** Periodic scans need a real scheduler (cron, a cloud scheduler, a workflow engine) with overlap protection (a lock per project) and catch-up behavior after downtime. The agent must still never send outside working hours.

**State persistence.** SQLite is fine for one process. Use Postgres (or similar) for concurrency, backups and migrations. Keep the split: remote systems hold official records; the agent DB holds follow-ups, approvals, commitments and history.

**Timezones and business calendars.** Per-person timezones and working hours, regional holidays, PTO from HR/calendar systems. Store timestamps with offsets (as this repo does) and convert at the edges.

**Monitoring and tracing.** Trace IDs already flow through logs. In production, ship structured logs to a log platform, add metrics (follow-ups sent, reply latency, approvals pending, tool errors, model latency/cost) and alerts.

**Approval auditing.** Keep approval records immutable (append-only), including who approved, when, the exact payload hash and the execution result. Consider requiring re-authentication for high-impact approvals and expiring stale approvals.

**Rate limits.** Centralize API calls through a client that honors `Retry-After` / `retry_in_seconds`, batches reads, and caches board snapshots briefly.

**Follow-up frequency controls.** Make policy configurable per team. Add quiet hours, per-person preferences and a global "pause all reminders" switch. Measure annoyance: replies like "stop", repeated unanswered follow-ups.

**Security and privacy.** Treat all content as untrusted (prompt injection). Minimize data sent to the model (IDs and the fields needed). Redact PII in logs. Set data retention. Review the model provider's data handling terms.

**Conversation context.** Long threads need summarization with provenance. Never lose which message a fact came from.

**Recipient validation.** Re-validate recipients at send time against the live directory (people leave, Slack IDs change). Block external domains unless explicitly approved.

**Error recovery.** Executors must be idempotent (this repo uses the approval ID as the key). Record failures on the approval and keep it retryable. Never "retry" by creating a new approval silently.

**Duplicate message prevention.** Idempotency keys on every write, unique constraints on event IDs, dedupe keys on proposals and questions (all implemented here in SQLite constraints).

**Source-of-truth synchronization.** Re-read the board before proposing and before applying (optimistic version check, as here). On conflict, re-propose rather than overwrite. Never maintain a shadow task status.

**Model and API costs.** Keep the deterministic engine doing the math so the model sees small, relevant tool outputs. Cap iterations/tool calls (configured). Choose a smaller model for reply interpretation, track tokens per run (`usage` is recorded), and cache investigation results within a scan.
