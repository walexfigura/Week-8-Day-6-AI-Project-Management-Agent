# Two-Way Communications

```mermaid
sequenceDiagram
    participant A as Agent
    participant P as Python rules
    participant H as Human approver
    participant S as Slack (mock)
    participant R as Sarah (simulated)
    A->>P: draft_slack_message(TM-SARAH, TASK-002, text)
    P->>P: eligibility, recipient, lint, dedupe
    P-->>A: FU-… pending approval APR-…
    H->>P: approve APR-… (exact text + recipient, hash bound)
    H->>P: apply-approved APR-…
    P->>S: record outgoing DM (idempotency key = APR-…)
    R->>S: reply in thread (event EVT-SARAH-0001)
    S->>P: process_incoming_reply
    P->>A: interpret (model) or rules
    A-->>P: structured interpretation
    P->>P: validate, re-resolve date, record commitment, impact
    P->>H: proposals: TASK-002 due date + note; notices to James/Kevin; decisions for Alex
```

## 1. Drafting
`create_followup_draft` (used by the model's draft tools and the offline engine):
- runs `evaluate_followup` for status checks (duplicates, wait windows, spacing, daily cap, exemptions, attempt limit, fresh updates),
- validates the recipient (verified, on-project, has a DM channel / approved address),
- lints the message (length, banned phrases, addressed by name, no claimed actions),
- dedupes on `(purpose, task, recipient)`,
- stores an `agent_followups` record and an `agent_approvals` request with the exact payload.

## 2. Approval and mock send
`approve` binds the human decision to the payload hash. `apply-approved` re-verifies and writes an `outgoing` row to `slack_messages` whose `thread_id` is its own ID (the start of a thread). The follow-up gets `sent_at`, `external_message_id`, `thread_id`, `reply_status=awaiting`, and `next_allowed_at`. Outside working hours the send is **deferred**.

## 3. Incoming replies
`simulate-reply` plays Slack's role: it injects the scenario's reply into the follow-up's thread with a stable `event_id`, then calls `process_incoming_reply`, the function a real webhook handler would call.

Association order: **thread of our follow-up** (high confidence) > explicit `TASK-###` > sender's single open task (medium). Never by a name in the text. Unverified senders (e.g. a Slack Connect guest, a vendor email) are recorded and ignored.

## 4. Interpretation
Either the model (`interpret_reply`, structured submit tool) or the offline `rules_interpreter`. Python then:
- validates fields and task IDs,
- re-resolves the date expression against the simulated clock (Python wins on disagreement and notes it),
- keeps provenance-based task association if the interpreter disagrees,
- forces `completion_confirmed = false` (a message can't confirm completion).

## 5. What gets created
| Intent | Records | Pending approvals / decisions |
|---|---|---|
| timeline_update | commitment (estimate), blocker | due-date + note proposal; downstream notices; lead decisions for knock-on slips |
| commitment_confirmation | commitment (confirmed_date) | proposal only if the date differs from the board |
| blocker_report | blocker | note proposal; clarification draft |
| completion_claim | commitment (claim) | note-only proposal (status unchanged); clarification |
| deadline_change_request | commitment (change request) | lead decision; clarification |
| delivery_claim / ambiguous | commitment (claim) where relevant | clarification |
| ownership_redirect | - | lead decision; clarification |

## 6. Idempotency
- Same event twice: `agent_processed_events` returns the stored result (`duplicate_event: true`).
- Same Slack event delivered twice: the message is stored once (`event_id` is UNIQUE).
- Proposals have `dedup_key = event:task`. Downstream notices have `question_key = downstream_notice:task:upstream:date`.
- Re-applying an approval returns `already_executed`. A direct retry of the send returns `duplicate_suppressed`.

## 7. History
`python run.py history` shows the outgoing/incoming log, approved board changes, commitments (with superseded ones), processed events and health history.
