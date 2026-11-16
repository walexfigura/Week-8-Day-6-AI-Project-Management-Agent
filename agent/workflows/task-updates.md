# Workflow: Task updates

Board changes are **proposals** until a human approves them.

- Allowed fields: `status`, `due_date`, `owner_id`, `estimated_workdays`, `add_update` (a note on the item).
- Every proposal is bound to the task's current version. If the task changes before approval, the proposal is rejected as stale and must be re-made.
- Timeline update from the owner, so propose `due_date` = owner's estimate plus a note that says it is an estimate and why it moved.
- Completion claim, so propose a note only. Status stays until the Done definition is met (`task-status-definitions.md`).
- Deadline-change request or a knock-on slip in another task, so raise a **decision** for the project lead instead of proposing new dates yourself.
- Reassignment, so a decision for the lead plus confirmation from the named person. Never propose it from a mention alone.
- Monday.com remains the source of truth. The agent's database tracks proposals, approvals and history, not a competing task status.
