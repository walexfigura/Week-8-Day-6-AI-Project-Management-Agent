"""Transparent schedule projection (a forward pass over working days).

For each unfinished task we project a start and finish date:

    start  = later of (today, the next workday after every dependency's projected finish)
    finish = start + remaining estimated workdays - 1

Owner estimates override the plan for a task's finish date and are labeled as
estimates. An overdue task with no current estimate is assumed to finish *today*
at the earliest - an optimistic, clearly-labeled assumption, not a fact.

This is NOT a full critical-path method: there are no resource constraints, no
partial-overlap modelling and no probabilistic durations. It is a deadline-risk
check that shows its working, which is what a project lead needs to decide
whether to escalate.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from typing import Any

from ..clock import BusinessCalendar
from .graph import DependencyGraph


@dataclass
class Projection:
    task_id: str
    status: str
    due_date: str
    projected_start: str | None
    projected_finish: str
    basis: str                 # done | owner_estimate | plan | assumed_today_no_estimate
    slack_workdays: int        # due - projected finish (negative = projected late)
    driving_dependency: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def project_schedule(graph: DependencyGraph, tasks: dict[str, dict[str, Any]], today: date, calendar: BusinessCalendar,
                     estimates: dict[str, date] | None = None) -> dict[str, Projection]:
    estimates = estimates or {}
    out: dict[str, Projection] = {}
    first_workday = calendar.on_or_next_workday(today)
    for tid in graph.topological_order():
        t = tasks[tid]
        due = date.fromisoformat(t["due_date"])
        if t["status"] == "Done":
            finish = date.fromisoformat(t.get("completed_at") or t["due_date"])
            out[tid] = Projection(tid, t["status"], t["due_date"], None, finish.isoformat(), "done",
                                  calendar.workdays_between(finish, due), None)
            continue
        dep_finishes = [(date.fromisoformat(out[d].projected_finish), d) for d in graph.direct_dependencies(tid)
                        if out[d].status != "Done"]
        driving = max(dep_finishes)[1] if dep_finishes else None
        start = first_workday
        if dep_finishes:
            start = max(start, calendar.next_workday(max(dep_finishes)[0]))
        if tid in estimates:
            finish = max(estimates[tid], start)
            basis = "owner_estimate"
        else:
            finish = calendar.add_workdays(start, max(int(t.get("estimated_workdays") or 1), 1))
            basis = "plan"
            if due < today and not dep_finishes:
                basis = "assumed_today_no_estimate"
        out[tid] = Projection(tid, t["status"], t["due_date"], start.isoformat(), finish.isoformat(), basis,
                              calendar.workdays_between(finish, due), driving)
    return out


def current_estimates(store: Any) -> dict[str, date]:
    """Latest non-superseded owner estimate per task (from processed replies)."""
    out: dict[str, date] = {}
    for c in store.list_commitments(current_only=True):
        if c["commitment_date"] and c["kind"] in {"estimate", "confirmed_date"}:
            task = store.get_task(c["task_id"])
            if task and task["status"] != "Done":
                out[c["task_id"]] = date.fromisoformat(c["commitment_date"])
    return out


def driving_chain(projections: dict[str, Projection], end_task: str) -> list[str]:
    """Follow driving dependencies backwards from end_task: the chain currently setting its date."""
    chain = [end_task]
    cur = projections[end_task].driving_dependency
    while cur:
        chain.append(cur)
        cur = projections[cur].driving_dependency
    return list(reversed(chain))
