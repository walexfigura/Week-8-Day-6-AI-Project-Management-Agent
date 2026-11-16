"""Follow-up prioritization with a transparent, explainable score.

Not "most days overdue first". Each component is listed so a human can see why a
task ranked where it did. Weights live here on purpose: change them and re-run.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from ..clock import BusinessCalendar, parse_dt
from ..dependencies.graph import DependencyGraph
from ..dependencies.scheduling import Projection
from ..models import PRIORITIES

WEIGHTS = {
    "per_downstream_task": 1.5,      # each open task waiting (transitively) on this one
    "per_direct_dependent_blocked": 2.0,
    "milestone_downstream": 3.0,     # a milestone sits downstream
    "milestone_slack_tight": 3.0,    # ...and its projected slack is <= warning threshold
    "overdue": 4.0,
    "overdue_per_day_cap3": 0.5,
    "blocked_status": 2.0,
    "business_priority": 1.0,        # x PRIORITIES value (Critical=4 ... Low=0)
    "no_estimate": 2.0,              # overdue/at-risk with no current owner estimate
    "fresh_update_available": -6.0,  # someone already told us what's going on
    "recently_contacted": -8.0,      # we asked recently; wait for the answer
    "partial_progress_possible": -1.0,
}


def rank_attention(store: Any, cal: BusinessCalendar, policy: dict[str, Any], projections: dict[str, Projection],
                   graph: DependencyGraph) -> list[dict[str, Any]]:
    now: datetime = store.now()
    today: date = now.date()
    fp = policy["followups"]
    tasks = {t["task_id"]: t for t in store.list_tasks()}
    project = store.get_project(store.default_project_id()) or {}
    milestone_tasks = {m["task_id"] for m in project.get("milestones", [])}
    warn = policy["escalation"]["milestone_slack_warning_workdays"]
    estimates = {c["task_id"] for c in store.list_commitments(current_only=True) if c["commitment_date"]}
    from .overdue_tasks import last_owner_update

    items = []
    for tid, t in tasks.items():
        if t["status"] == "Done":
            continue
        due = date.fromisoformat(t["due_date"])
        overdue = due < today
        downstream = [d for d in graph.transitive_dependents(tid) if tasks[d]["status"] != "Done"]
        direct = graph.direct_dependents(tid)
        direct_blocked = [d for d in direct if tasks[d]["status"] in {"Blocked", "Waiting"}]
        ms_down = [m for m in milestone_tasks if m in downstream]
        tight = [m for m in ms_down if projections[m].slack_workdays <= warn]
        approaching = due >= today and cal.workdays_between(today, due) <= fp["approaching_due_workdays"]
        projected_late = projections[tid].slack_workdays < 0
        last = last_owner_update(store, tid)
        fresh = bool(last and (now - parse_dt(last["at"])).total_seconds() / 3600 <= fp["fresh_update_hours"])
        recent_fu = [f for f in store.list_followups(task_id=tid, recipient_id=t["owner_id"])
                     if f["status"] in {"pending_approval", "approved", "sent"}]
        needs_attention = overdue or t["status"] in {"Blocked", "Waiting", "At Risk"} or approaching or projected_late
        if not needs_attention:
            continue
        comp: dict[str, float] = {}
        if downstream:
            comp["downstream_tasks"] = WEIGHTS["per_downstream_task"] * len(downstream)
        if direct_blocked:
            comp["direct_dependents_blocked"] = WEIGHTS["per_direct_dependent_blocked"] * len(direct_blocked)
        if ms_down:
            comp["milestone_downstream"] = WEIGHTS["milestone_downstream"]
        if tight:
            comp["milestone_slack_tight"] = WEIGHTS["milestone_slack_tight"]
        if overdue:
            comp["overdue"] = WEIGHTS["overdue"] + WEIGHTS["overdue_per_day_cap3"] * min((today - due).days, 3)
        if t["status"] == "Blocked":
            comp["blocked_status"] = WEIGHTS["blocked_status"]
        comp["business_priority"] = WEIGHTS["business_priority"] * PRIORITIES.get(t.get("priority", "Medium"), 1)
        if (overdue or projected_late) and tid not in estimates:
            comp["no_current_estimate"] = WEIGHTS["no_estimate"]
        if fresh:
            comp["fresh_update_available"] = WEIGHTS["fresh_update_available"]
        if recent_fu:
            comp["recently_contacted"] = WEIGHTS["recently_contacted"]
        if t.get("partial_progress"):
            comp["partial_progress_possible"] = WEIGHTS["partial_progress_possible"]

        # A blocked/waiting task whose blocker is an open upstream task is "covered" by asking upstream first.
        open_upstream = [u for u in graph.direct_dependencies(tid) if tasks[u]["status"] != "Done"]
        upstream_problem = [u for u in open_upstream if date.fromisoformat(tasks[u]["due_date"]) < today
                            or tasks[u]["status"] in {"Blocked", "At Risk"} or projections[u].slack_workdays < 0]
        driver = projections[tid].driving_dependency
        if not upstream_problem and projected_late and not overdue and driver and driver in estimates:
            upstream_problem = [driver]  # late only because of an upstream owner's estimate
        covered_by = upstream_problem[0] if upstream_problem else None
        items.append({
            "task_id": tid, "name": t["name"], "owner_id": t["owner_id"], "status": t["status"], "due_date": t["due_date"],
            "score": round(sum(comp.values()), 2), "components": comp,
            "overdue": overdue, "approaching_due": approaching, "projected_late": projected_late,
            "downstream_open": downstream, "direct_dependents": direct,
            "fresh_update": {"id": last["id"], "at": last["at"]} if fresh and last else None,
            "covered_by_upstream": covered_by,
            "recent_followup": recent_fu[-1]["followup_id"] if recent_fu else None,
        })
    items.sort(key=lambda i: (-i["score"], i["task_id"]))
    return items
