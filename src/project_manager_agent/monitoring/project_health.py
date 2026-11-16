"""Project health assessment with transparent rules (not a probability of success).

Classification precedence: Blocked > At Risk > Needs Review > On Track.

* Blocked      - a milestone is projected to slip, the dependency graph is invalid
                 (cycle / unknown task), or a task with dependents has no owner.
* At Risk      - an overdue task has open downstream work, a task feeding a milestone is
                 projected past its due date, or a milestone's projected slack is at or
                 below the warning threshold.
* Needs Review - information problems: date conflicts, stale updates, unanswered
                 follow-ups, overdue tasks with no estimate, open management decisions.
* On Track     - none of the above.
"""

from __future__ import annotations

from collections import Counter
from datetime import date
from typing import Any

from ..clock import BusinessCalendar, parse_dt
from ..dependencies.graph import CycleError, DependencyGraph, InvalidReferenceError
from ..dependencies.scheduling import current_estimates, driving_chain, project_schedule
from .overdue_tasks import approaching_tasks, date_conflicts, overdue_tasks, stale_tasks
from .priorities import rank_attention


def unanswered_followups(store: Any, cal: BusinessCalendar, policy: dict[str, Any]) -> list[dict[str, Any]]:
    now = store.now()
    out = []
    for f in store.list_followups(statuses={"sent"}):
        if f["reply_status"] == "awaiting" and f["sent_at"]:
            due_reply = cal.add_working_hours(parse_dt(f["sent_at"]), policy["followups"]["reply_wait_working_hours"])
            if now >= due_reply:
                out.append({**f, "reply_overdue_since": due_reply.isoformat()})
    return out


def assess_health(store: Any, policy: dict[str, Any], *, record: bool = False) -> dict[str, Any]:
    cal = BusinessCalendar(policy)
    now = store.now()
    today: date = now.date()
    project_id = store.default_project_id()
    project = store.get_project(project_id)
    tasks_list = store.list_tasks(project_id)
    tasks = {t["task_id"]: t for t in tasks_list}
    graph = DependencyGraph.from_store(store)

    reasons: dict[str, list[str]] = {"Blocked": [], "At Risk": [], "Needs Review": []}
    graph_problem = None
    try:
        graph.validate()
    except (CycleError, InvalidReferenceError) as exc:
        graph_problem = str(exc)
        reasons["Blocked"].append(f"Dependency graph is invalid: {exc}")
    if graph_problem:
        return {"project_id": project_id, "project_name": project["name"], "as_of": now.isoformat(),
                "classification": "Blocked", "reasons": reasons, "graph_error": graph_problem, "metrics": {},
                "tasks": tasks_list}

    estimates = current_estimates(store)
    proj = project_schedule(graph, tasks, today, cal, estimates)
    warn = policy["escalation"]["milestone_slack_warning_workdays"]
    overdue = overdue_tasks(tasks_list, today)
    approaching = approaching_tasks(tasks_list, today, cal, policy["followups"]["approaching_due_workdays"])
    stale = stale_tasks(store, now, policy["followups"]["stale_update_days"])
    conflicts = date_conflicts(store)
    unanswered = unanswered_followups(store, cal, policy)
    decisions = store.list_decisions()
    pending_approvals = store.list_approvals({"pending"})
    priorities = rank_attention(store, cal, policy, proj, graph)

    milestones = []
    for ms in project.get("milestones", []):
        p = proj[ms["task_id"]]
        milestones.append({**ms, "projected_finish": p.projected_finish, "slack_workdays": p.slack_workdays,
                           "projected_to_slip": p.slack_workdays < 0, "driving_chain": driving_chain(proj, ms["task_id"])})
    feeding = set()
    for ms in milestones:
        feeding |= set(graph.transitive_dependencies(ms["task_id"]))
    projected_late = [p.to_dict() | {"name": tasks[p.task_id]["name"], "owner_id": tasks[p.task_id]["owner_id"]}
                      for p in proj.values() if p.status != "Done" and p.slack_workdays < 0]

    bottlenecks = []
    for t in tasks_list:
        if t["status"] == "Done":
            continue
        waiting = [d for d in graph.direct_dependents(t["task_id"]) if tasks[d]["status"] != "Done"]
        # A bottleneck is a task whose *own* delay holds up several others (not a task blocked upstream).
        held = [d for d in waiting if tasks[d]["status"] in {"Blocked", "Waiting"}]
        if len(waiting) >= 2 and (any(o["task_id"] == t["task_id"] for o in overdue) or held
                                  or proj[t["task_id"]].slack_workdays < 0 or t["status"] == "At Risk"):
            bottlenecks.append({"task_id": t["task_id"], "name": t["name"], "owner_id": t["owner_id"],
                                "blocking": waiting, "downstream_total": len(graph.transitive_dependents(t["task_id"]))})

    for ms in milestones:
        if ms["projected_to_slip"]:
            reasons["Blocked"].append(f"Milestone '{ms['name']}' projected {ms['projected_finish']} vs due {ms['due_date']}.")
    for t in tasks_list:
        if t["status"] != "Done" and not t.get("owner_id") and graph.direct_dependents(t["task_id"]):
            reasons["Blocked"].append(f"{t['task_id']} blocks other work and has no owner.")
    for o in overdue:
        open_down = [d for d in graph.transitive_dependents(o["task_id"]) if tasks[d]["status"] != "Done"]
        if open_down:
            reasons["At Risk"].append(f"{o['task_id']} ({o['name']}) is {o['days_overdue']} day(s) overdue with "
                                      f"{len(open_down)} open downstream task(s).")
    overdue_ids = {o["task_id"] for o in overdue}
    for p in projected_late:
        if p["task_id"] in feeding and p["task_id"] not in overdue_ids:
            reasons["At Risk"].append(f"{p['task_id']} ({p['name']}) projected {p['projected_finish']}, "
                                      f"{-p['slack_workdays']} workday(s) after its due date {p['due_date']}.")
    for ms in milestones:
        if not ms["projected_to_slip"] and ms["slack_workdays"] <= warn:
            reasons["At Risk"].append(f"Milestone '{ms['name']}' has only {ms['slack_workdays']} workday(s) of projected slack.")
    for c in conflicts:
        reasons["Needs Review"].append(f"{c['task_id']}: board due {c['board_due_date']} but {c['source']} says "
                                       f"{c['reported_date']} ({c['kind']}).")
    for s in stale:
        reasons["Needs Review"].append(f"{s['task_id']} has no owner update for {s['days_since_update']} day(s).")
    for f in unanswered:
        reasons["Needs Review"].append(f"Follow-up {f['followup_id']} to {f['recipient_id']} on {f['task_id']} is unanswered.")
    for o in overdue:
        if o["task_id"] not in estimates:
            reasons["Needs Review"].append(f"{o['task_id']} is overdue and has no current estimate from its owner.")
    for d in decisions:
        reasons["Needs Review"].append(f"Open decision {d['decision_id']}: {d['question']}")

    classification = next((c for c in ("Blocked", "At Risk", "Needs Review") if reasons[c]), "On Track")
    status_counts = Counter(t["status"] for t in tasks_list)
    metrics = {
        "total_tasks": len(tasks_list),
        "completed_tasks": status_counts.get("Done", 0),
        "overdue_tasks": len(overdue),
        "blocked_tasks": status_counts.get("Blocked", 0),
        "waiting_tasks": status_counts.get("Waiting", 0),
        "tasks_at_risk": len({p["task_id"] for p in projected_late} | {t["task_id"] for t in tasks_list if t["status"] == "At Risk"}),
        "approaching_due": len(approaching),
        "stale_tasks": len(stale),
        "unanswered_followups": len(unanswered),
        "pending_approvals": len(pending_approvals),
        "open_decisions": len(decisions),
        "status_counts": dict(status_counts),
        "upcoming_milestones": [{"name": m["name"], "due_date": m["due_date"], "projected_finish": m["projected_finish"],
                                 "slack_workdays": m["slack_workdays"]} for m in milestones],
    }
    result = {
        "project_id": project_id, "project_name": project["name"], "as_of": now.isoformat(),
        "classification": classification, "reasons": reasons, "metrics": metrics,
        "overdue": overdue, "approaching": approaching, "stale": stale, "conflicts": conflicts,
        "unanswered_followups": unanswered, "decisions": decisions, "pending_approvals": pending_approvals,
        "bottlenecks": bottlenecks, "projected_late": projected_late, "milestones": milestones,
        "projections": {k: v.to_dict() for k, v in proj.items()}, "priorities": priorities,
        "estimates_used": {k: v.isoformat() for k, v in estimates.items()},
        "tasks": tasks_list,
        "method_note": "Rule-based classification over a forward-pass schedule projection. Not a probability of success.",
    }
    if record:
        store.insert_health(project_id, classification, metrics)
    return result
