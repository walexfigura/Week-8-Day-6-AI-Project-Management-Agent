"""Downstream impact of a (possible) change to one task's finish date."""

from __future__ import annotations

from datetime import date
from typing import Any

from ..clock import BusinessCalendar, fmt_day
from .graph import DependencyGraph
from .scheduling import current_estimates, driving_chain, project_schedule


def milestone_task_ids(store: Any, project_id: str) -> list[dict[str, Any]]:
    project = store.get_project(project_id) or {}
    return project.get("milestones", [])


def analyze_impact(store: Any, calendar: BusinessCalendar, task_id: str, new_finish: date | None = None) -> dict[str, Any]:
    task = store.get_task(task_id)
    if task is None:
        raise KeyError(f"Unknown task {task_id!r}")
    graph = DependencyGraph.from_store(store)
    tasks = {t["task_id"]: t for t in store.list_tasks()}
    today = store.now().date()
    base_estimates = current_estimates(store)
    baseline = project_schedule(graph, tasks, today, calendar, base_estimates)
    revised_estimates = dict(base_estimates)
    if new_finish:
        revised_estimates[task_id] = new_finish
    revised = project_schedule(graph, tasks, today, calendar, revised_estimates)

    downstream = graph.transitive_dependents(task_id)
    affected = []
    for tid in downstream:
        b, r = baseline[tid], revised[tid]
        t = tasks[tid]
        affected.append({
            "task_id": tid, "name": t["name"], "owner_id": t["owner_id"], "status": t["status"],
            "direct_dependent": tid in graph.direct_dependents(task_id),
            "due_date": t["due_date"],
            "baseline_projected_finish": b.projected_finish, "revised_projected_finish": r.projected_finish,
            "revised_slack_workdays": r.slack_workdays,
            "would_miss_due_date": r.slack_workdays < 0,
            "partial_progress_possible": t.get("partial_progress"),
        })

    milestones = []
    for ms in milestone_task_ids(store, task["project_id"]):
        mt = ms["task_id"]
        if mt not in revised:
            continue
        b, r = baseline[mt], revised[mt]
        milestones.append({
            "milestone_id": ms["milestone_id"], "name": ms["name"], "task_id": mt, "due_date": ms["due_date"],
            "downstream_of_task": mt in downstream,
            "baseline_projected_finish": b.projected_finish, "revised_projected_finish": r.projected_finish,
            "baseline_slack_workdays": b.slack_workdays, "revised_slack_workdays": r.slack_workdays,
            "projected_to_slip": r.slack_workdays < 0,
            "driving_chain": driving_chain(revised, mt),
        })

    direct = [a for a in affected if a["direct_dependent"]]
    late = [a for a in affected if a["would_miss_due_date"]]
    slipping_ms = [m for m in milestones if m["projected_to_slip"]]
    lines = []
    if new_finish:
        lines.append(f"If {task_id} finishes {fmt_day(new_finish)} (estimate, not confirmed):")
    lines.append(f"{len(downstream)} downstream task(s); {len(direct)} depend on it directly "
                 f"({', '.join(a['task_id'] for a in direct) or 'none'}).")
    if late:
        lines.append("Projected past their own due dates: " +
                     ", ".join(f"{a['task_id']} ({a['revised_slack_workdays']} workday(s))" for a in late) + ".")
    else:
        lines.append("No downstream task is projected past its own due date.")
    for m in milestones:
        verdict = "projected to SLIP" if m["projected_to_slip"] else "still feasible"
        lines.append(f"Milestone {m['name']} ({m['due_date']}): {verdict}; slack {m['baseline_slack_workdays']} -> "
                     f"{m['revised_slack_workdays']} workday(s).")
    return {
        "task_id": task_id, "task_name": task["name"], "new_finish": new_finish.isoformat() if new_finish else None,
        "basis": "owner estimate (unconfirmed)" if new_finish else "current plan and known estimates",
        "downstream_task_ids": downstream, "direct_dependents": [a["task_id"] for a in direct],
        "affected_tasks": affected, "milestones": milestones,
        "launch_at_risk": bool(slipping_ms) or any(m["revised_slack_workdays"] <= 1 for m in milestones if m["downstream_of_task"]),
        "launch_projected_to_slip": bool(slipping_ms),
        "summary": " ".join(lines),
        "method": "Forward-pass projection over working days. Not a full critical-path analysis; see docs/dependency-management.md.",
    }
