# Response Handling

Return a structured interpretation for every reply:

```json
{
  "related_task_id": "TASK-002",
  "message_intent": "timeline_update",
  "new_estimated_completion": "2026-03-11",
  "date_expression": "Wednesday",
  "blocker": "Photographer had to reschedule",
  "completion_confirmed": false,
  "requires_task_update": true,
  "requires_human_approval": true,
  "requires_clarification": false,
  "confidence": "high",
  "uncertainties": ["Owner estimate, not delivery"]
}
```

## Association
Link a reply to a task by (1) the thread of the follow-up you sent, (2) an explicit task ID, (3) the sender's only open task, in that order. **Never** by a name that appears in the text. Unknown or unverified senders: record, don't act.

## Dates
Resolve relative to the **reply's timestamp**. "Wednesday" sent Tue 2026-03-10 is 2026-03-11. Ranges ("next week", "end of month") stay null. Don't pick a day. Prefer the committed date ("ready Monday 3/16, not Friday" means 3/16).

## Intents
| Reply | Intent | Do |
|---|---|---|
| "We'll have the final images on Wednesday." | timeline_update | Propose new due date + note; check downstream; notify dependents after approval |
| "Should be done tomorrow." | timeline_update (tentative) | Estimate only |
| "If images land Wed I can still finish by the 16th." | commitment_confirmation | Record; no change if it matches the board |
| "Still waiting on design." | blocker_report | Record blocker; ask what exactly and a rough date |
| "I think we're good." | ambiguous | Ask: delivered, or what date? |
| "Can we push it to next week?" | deadline_change_request | Decision for the project lead; ask for a specific date |
| "Done, just needs review." | completion_claim | Note only; status stays; ask who reviews and when |
| "I sent this yesterday." | delivery_claim | Unverified; ask where it went |
| "Ask Kevin, he's handling it now." | ownership_redirect | Don't reassign; lead decision + confirm with Kevin |

`completion_confirmed` is false for any chat message. Completion is confirmed by the board record or a verified deliverable.
