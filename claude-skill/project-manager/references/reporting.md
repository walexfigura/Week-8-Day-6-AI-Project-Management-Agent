# Report Format

```markdown
# Project Health Briefing: <project>
*As of <date/time> · Classification: **<On Track|At Risk|Blocked|Needs Review>***

## Executive summary
2–4 sentences: classification, whether the main milestone is projected to slip or only lost slack, the main bottleneck, decisions needed.

### Why this classification
- **At Risk:** <rule-based reason with task IDs>

## Key metrics
| Total | Completed | Overdue | Blocked | At risk | Upcoming milestones (due / projected / slack) |

## Most important blockers
- **TASK-ID name** (owner, status, due) blocks <tasks + owners>. Latest commitment: <estimate + source>.

## Latest updates
- `Mar 10 09:55` <who> <what> (<message/task ID>)

## Pending decisions
- Approvals waiting; questions only the project lead can answer.

## Recommended next steps
1. <concrete action, owner, why>

## Source references
| Task | Owner | Status | Due | Projected | Basis | Latest signal |
```

Rules: label estimates and assumptions; cite IDs; don't present the classification as a probability; don't report anything as sent or changed unless confirmed.
