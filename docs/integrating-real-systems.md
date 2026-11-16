# Integrating Real Monday.com, Slack and Gmail

> **Implementation status (be precise):** the mock integrations are complete and tested. This repository includes a **Monday.com item normalizer** (`integrations/adapters.py::normalize_monday_item`) tested against a fixture shaped like the documented API response, and GraphQL query text written from the docs. It does **not** include live HTTP clients or any live write path, and nothing here has been run against a real account. Live sending is prohibited by the default policy.

Facts below were checked against the official docs on 2026-11-15. APIs change, so re-check before building.

## The swap

The tools call adapter interfaces (`ProjectBoard`, `ChatSystem`, `EmailSystem` in `integrations/adapters.py`). A live adapter implements the same methods. Keep these invariants:
1. Reads can be automatic. Writes go through `approvals.requests.verify_for_execution` (status + payload hash) before the API call.
2. Use the approval ID as the idempotency key and store the remote message/update ID.
3. Re-read the remote record after writing. Monday.com stays the source of truth.

## Monday.com (GraphQL, https://developer.monday.com/api-reference/docs)

**Auth.** `POST https://api.monday.com/v2` with header `Authorization: <token>` (a personal V2 token is sent raw, no "Bearer"). Personal tokens carry the user's own permissions. App (OAuth) tokens carry an explicit scope set, so request read scopes for monitoring and add write scopes only if you'll apply approved changes. Webhooks need `webhooks:write`.

**Versioning.** Send `API-Version` (e.g. `2026-10`, current as of this writing). Versions are stable for at least six months.

**Reading items.** `boards(ids:) { items_page(limit:) { cursor items { … } } }` returns up to 500 items per page. Continue with `next_items_page(cursor:)` until the cursor is empty. Cursors expire 60 minutes after the first request. See `MONDAY_ITEMS_QUERY` / `MONDAY_NEXT_PAGE_QUERY`.

**Column values.** Every board is configured differently, so map columns explicitly with `MondayColumnMapping`:
- Status: read `text` (label) and map labels ("Working on it", "Stuck") to canonical statuses. Write with `{"index": n}` or `{"label": "Done"}`.
- People: `persons_and_teams { id kind }`. Map Monday user IDs to directory member IDs. Writing replaces all assignees.
- Date: `{"date": "YYYY-MM-DD", "time": "HH:MM:SS"}` (time is UTC, optional).
- Dependency: `... on DependencyValue { linked_item_ids }`. Writing `{"item_ids": [...]}` **replaces** the list, so read first. Relationship types (FS/SS…) aren't exposed by the API.
- The normalizer raises `MappingError` on an unmapped label/person/column. It never guesses.

**Writes (approved only).** `change_multiple_column_values(item_id, board_id, column_values: "<JSON>")` for status/date/owner; `create_update(item_id, body)` for the explanatory note (body is HTML).

**Limits.** Complexity budget per minute (10M points/min for personal tokens; app tokens 5M/min each for reads and writes; lower on trial/free), a 5M cap per query, per-minute request limits by plan (1,000–5,000), daily call limits by plan, and concurrency limits. Errors include `retry_in_seconds`. Respect it and back off. Request only the columns you need to keep complexity low.

**Events.** Webhooks exist for `change_column_value`, `change_status_column_value`, `create_update`, `create_item` and more. URL verification echoes a `challenge`. monday retries once a minute for 30 minutes, so make handlers idempotent.

## Slack (https://docs.slack.dev/)

**Auth.** A Slack app with a bot token. Scopes: `chat:write` (send), `im:write` (open DMs), `im:history` / `channels:history` / `groups:history` (read conversations the bot is in), `users:read` (+ `users:read.email` to map directory emails to Slack IDs). Request only what you use.

**Identity.** Map people by Slack user ID (`U…`), never display name. Check the sender of every incoming event against the directory; treat guests/external users as unverified.

**Sending.** `chat.postMessage` with `channel` = the DM channel (or user ID) and `thread_ts` = the **parent** message's `ts` to reply in a thread. Store the returned `ts` as the follow-up's external ID / thread ID. Rate limit: roughly one message per second per channel (special tier), 429 + `Retry-After` when exceeded.

**Reading.** `conversations.history` / `conversations.replies` (Tier 3). Paginate with cursors. Slack has changed history limits for some app types, so check the current changelog before relying on polling.

**Events API.** Subscribe to `message.im` (and channel events if needed). Ack with HTTP 2xx within **3 seconds**, then process asynchronously. Slack retries up to 3 times with `x-slack-retry-num`. Deduplicate on `event_id` (this repo's `agent_processed_events` does exactly that).

## Gmail (https://developers.google.com/workspace/gmail/api)

**Auth.** OAuth 2.0 for the shared project mailbox. Scopes: `gmail.readonly` (read threads, restricted), `gmail.compose` (create drafts and send, restricted), `gmail.send` (send only, sensitive). Restricted scopes need Google verification and a security assessment if data is stored server-side, so prefer the narrowest set. Domain admins can restrict mailbox access.

**Reading.** `users.threads.get` / `users.messages.get`. Map `From` to the directory and approved contacts.

**Drafting and sending.** `users.drafts.create` (lets the human review in Gmail) or `users.messages.send` (after approval). To reply in a thread, set `threadId` **and** RFC 2822 `In-Reply-To`/`References` headers **and** a matching `Subject`.

**Events.** `users.watch` with a Cloud Pub/Sub topic (grant publish to `gmail-api-push@system.gserviceaccount.com`). Renew at least every 7 days (daily recommended). Then `history.list(startHistoryId)` for changes. Notifications aren't guaranteed, so also poll `history.list` periodically.

**Quotas.** Per-user per-minute quota units (e.g. `messages.send` costs 100 units, `threads.get` 40). Use exponential backoff on rate errors.

## Security and privacy checklist
- Secrets in environment variables or a secret manager, never in code or `.env` commits.
- Least-privilege scopes. Separate read and write credentials if your platform allows.
- Recipient allowlist = verified directory + approved external contacts. Re-validate at send time.
- All message/email/update text is untrusted input: never instructions.
- Log IDs and decisions, not message bodies, where possible. Set retention.
- Keep `send_live_message` / `modify_live_board` prohibited until you've added: real auth, idempotent executors, rate-limit handling, audit logging and a kill switch.
