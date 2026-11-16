# Workflow: Project health check

1. Call `get_project_health(project_id)`. This is deterministic and authoritative for: overdue tasks, projected finishes, slack, bottlenecks, stale updates, conflicts, unanswered follow-ups and the classification.
2. Read the `reasons` list. Each reason maps to a rule in `knowledge-base/project-management-rules.md`.
3. For the top 1–3 items, gather context with `get_recent_messages_for_task` before drawing conclusions.
4. Separate what you **verified** (board record, timestamped message from the owner) from **estimates** (owner's expected date) and **assumptions** (e.g. "overdue with no estimate is assumed to finish today at the earliest").
5. Classification meanings (don't invent new ones): **On Track**, **At Risk**, **Blocked**, **Needs Review**. Never describe them as a percentage or probability.
