# Project Management Rules

## Ownership
- **Project ownership:** the project lead (Alex Rivera) owns the launch date, milestones, scope and go/no-go. Only the project lead can approve moving a milestone.
- **Task ownership:** every open task has exactly one owner on the board. A task that blocks other work and has no owner makes the project **Blocked** (health rule).
- Reassignment requires project-lead approval *and* confirmation from the new owner. A message saying "Kevin's handling it" is a prompt to verify, not a reassignment.

## Due dates
- Due dates on Monday.com are commitments. Owner estimates ("Wednesday") are recorded as **estimates** until the board is updated through an approved change.
- A task is **overdue** when its due date is before today (simulated clock) and it is not Done.
- **Approaching** = due within `approaching_due_workdays` (2) working days.

## Status updates
- Owners update their tasks at least every `stale_update_days` (5) calendar days while In Progress, Blocked, Waiting or At Risk. Older than that is **stale** and gets a low-priority check-in.
- An owner's message in Slack or email counts as an update (with provenance), but it is not a board change.

## Dependency management
- Dependencies are finish-to-start and recorded on the board by task ID.
- Circular dependencies and references to unknown tasks are data errors. The project is classified **Blocked** until fixed.
- When a task is Blocked/Waiting because of an upstream task, follow up on the **upstream task**, not every downstream owner.

## Escalation
See `project-escalation-rules.md`.

## Health standards (implemented in `monitoring/project_health.py`)
| Classification | Triggered by |
|---|---|
| **Blocked** | A milestone projected to slip; invalid dependency graph; a blocking task with no owner |
| **At Risk** | An overdue task with open downstream work; a task feeding a milestone projected past its due date; milestone slack ≤ `milestone_slack_warning_workdays` (2) |
| **Needs Review** | Date conflicts between sources; stale updates; unanswered follow-ups; overdue tasks without an estimate; open lead decisions |
| **On Track** | None of the above |

Classifications are transparent rules, not probabilities of success.
