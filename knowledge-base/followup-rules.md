# Follow-up Rules

Values below live in `config/policy.json` (`followups`, `working_hours`, `working_days`, `holidays`, `exemptions`) and are enforced by `communications/followups.py::evaluate_followup`. Change the JSON to change behavior.

## When a reminder is appropriate
- The task is overdue, Blocked/Waiting/At Risk, due within 2 working days, projected late, or stale, **and**
- no fresh owner update exists (newer than `fresh_update_hours` = 48 h), **and**
- the question isn't already covered by asking an upstream owner.

## Spacing and limits
| Rule | Value |
|---|---|
| Minimum spacing between follow-ups to the same person about the same task | `min_working_hours_between_followups` = 8 working hours (≈ one working day) |
| Wait for a reply before anything else | `reply_wait_working_hours` = 8 working hours |
| Max follow-ups to one person per day | `max_followups_per_person_per_day` = 2 |
| Unanswered attempts before escalating instead | `max_attempts_before_escalation` = 2 |

Working hours count only 9:00–17:30 ET on working days, excluding holidays. A message approved outside working hours is **deferred**, not sent.

## Duplicates
- One open question per (purpose, task, recipient). A second draft returns the existing one ("deduplicated").
- An approved send that is retried returns the original message ID. Nothing is sent twice.
- A reply event processed twice returns the stored result. No second proposal is created.

## Pending replies
While a follow-up is awaiting a reply inside the wait window, the recommendation is **wait**. After the window, one more follow-up is allowed. After the attempt limit, the recommendation becomes **escalate**.

## One blocker, one conversation
If James and Kevin are both waiting on Sarah, ask Sarah. Tell James and Kevin what you learned (downstream notice) only after Sarah answers and a human approves.

## Exemptions
`python run.py exempt --member TM-EMMA --until 2026-03-13 --reason "offsite"` pauses reminders for a person or task.
