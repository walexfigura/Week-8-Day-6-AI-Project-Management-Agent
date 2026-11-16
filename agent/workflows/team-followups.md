# Workflow: Team follow-ups

For each person you are considering contacting:

1. **Already answered?** `get_recent_messages_for_task`. If the owner gave a usable update recently, don't ask.
2. **Already asked?** `get_pending_followups`. If a follow-up is queued or awaiting a reply, don't add another.
3. **Allowed?** `check_followup_eligibility(task_id, recipient_id, purpose)`. It checks duplicates, the minimum interval, reply windows, the daily cap, exemptions, the attempt limit (escalate instead) and working hours. Respect "wait", "skip" and "escalate".
4. **Right person?** The task owner from the board (a verified member ID), never a name guessed from message text.
5. **Right channel?** Slack for internal quick check-ins. Email for external contacts or formal topics (vendor, contract, budget).
6. **Draft.** `draft_slack_message` / `draft_email` with purpose `status_check`, `stale_check`, `downstream_notice`, `clarification` or `escalation`, a short reason, and evidence IDs.

Message recipe (under ~350 characters is ideal):
- Greeting with first name.
- What you're checking on, in plain words ("the product photos").
- Why it matters now (who is waiting, by first name).
- What you already know, so you don't ask something answered ("I saw Lumen's note about rescheduling").
- One clear question that produces a date or a decision.

The draft tool may refuse (lint errors, recipient checks, rules). Don't try to work around a refusal. Report it.
