# Workflow: Task prioritization

The `priorities` list from `get_project_health` is scored transparently (see `components`). Factors, roughly in order of weight:

1. Number of open downstream tasks (transitively) and direct dependents already Blocked/Waiting.
2. A milestone downstream, especially with tight projected slack.
3. Overdue status (capped, so "most days overdue" doesn't dominate).
4. Business priority (Critical > High > Medium).
5. No current estimate from the owner.
6. **Negative weights:** a fresh owner update already exists; we already contacted them recently; partial progress is possible.

Rules of thumb:
- If a task is Blocked/Waiting on an upstream problem (`covered_by_upstream`), do **not** chase its owner. Ask the upstream owner.
- If a fresh update answers the question (`fresh_update`), don't ask again; cite the update.
- Prefer one high-leverage question over several low-leverage ones.
