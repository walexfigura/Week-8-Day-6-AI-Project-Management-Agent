# Project Monitoring

For every task, record: ID, name, owner, status, priority, due date, dependencies, last update (who/when).

## Detect
- **Overdue:** due date before today and status not Done.
- **Approaching:** due within 2 working days.
- **Blocked/Waiting:** status says so. Find *what* it waits on (dependency or note).
- **Stale:** In Progress/Blocked/Waiting/At Risk with no owner update in 5+ days.
- **Conflicts:** a message states a different committed date than the board ("planning on the 18th" vs due 16th).
- **Unanswered follow-ups:** asked, no reply after one working day.

## Health classification (transparent rules, not probabilities)
| Class | When |
|---|---|
| Blocked | Milestone projected to slip; invalid/circular dependencies; a blocking task without an owner |
| At Risk | Overdue task with open downstream work; a milestone-feeding task projected past due; milestone slack ≤ 2 working days |
| Needs Review | Conflicts, stale updates, unanswered follow-ups, overdue tasks without an estimate, open lead decisions |
| On Track | None of the above |

Use the most severe that applies, and list every reason.

## Information freshness
An owner's message counts as an update (cite it), but it doesn't change the board. Updates newer than 48 hours usually answer "what's the status?". Don't ask again.
