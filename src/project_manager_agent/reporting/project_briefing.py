"""Project health briefing built from *persisted* state only (no model required).

Every number in the report comes from the database at the simulated time the
report is generated, so the report is reproducible and auditable.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from ..clock import fmt_day, parse_dt
from ..communications.commitments import describe
from ..communications.followups import select_followups
from ..dependencies.graph import DependencyGraph
from ..monitoring.project_health import assess_health


def _name(store: Any, member_id: str | None) -> str:
    if not member_id:
        return "unassigned"
    m = store.get_member(member_id) or store.get_contact(member_id)
    return m["name"] if m else member_id


def build_briefing(store: Any, policy: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    h = assess_health(store, policy, record=True)
    now = store.now()
    tasks = {t["task_id"]: t for t in h["tasks"]}
    m = h["metrics"]
    L: list[str] = []
    L.append(f"# Project Health Briefing: {h['project_name']}")
    L.append("")
    local = now.astimezone(ZoneInfo(policy.get("timezone", "America/New_York")))
    L.append(f"*As of {local.strftime('%A %B %d, %Y %H:%M %Z')} (simulated clock) · Classification: **{h['classification']}** · "
             "Fictional Northstar Supply data · Mock integrations*")
    L.append("")
    L.append("## Executive summary")
    L.append("")
    primary = next((ms for ms in h["milestones"] if ms.get("primary")), h["milestones"][-1] if h["milestones"] else None)
    bn = h["bottlenecks"]
    summary = [f"The project is **{h['classification']}**."]
    if primary:
        verdict = "is projected to slip" if primary["projected_to_slip"] else "is still feasible"
        summary.append(f"The {primary['name'].lower()} ({fmt_day(date.fromisoformat(primary['due_date']))}) {verdict}: projected "
                       f"{fmt_day(date.fromisoformat(primary['projected_finish']))}, {primary['slack_workdays']} workday(s) of slack.")
    if bn:
        b = bn[0]
        summary.append(f"The main bottleneck is {b['task_id']} ({b['name']}, {_name(store, b['owner_id'])}), which directly blocks "
                       f"{len(b['blocking'])} task(s) and {b['downstream_total']} downstream in total.")
    est = h["estimates_used"]
    if est:
        summary.append("Owner estimates in use (not confirmations): " +
                       ", ".join(f"{k} -> {fmt_day(date.fromisoformat(v))}" for k, v in est.items()) + ".")
    if m["pending_approvals"] or m["open_decisions"]:
        summary.append(f"{m['pending_approvals']} action(s) await approval and {m['open_decisions']} decision(s) need the project lead.")
    L.append(" ".join(summary))
    L.append("")
    L.append("### Why this classification")
    L.append("")
    for level in ("Blocked", "At Risk", "Needs Review"):
        for r in h["reasons"].get(level, []):
            L.append(f"- **{level}:** {r}")
    if not any(h["reasons"].values()):
        L.append("- No rule triggered.")
    L.append("")
    L.append("## Key metrics")
    L.append("")
    L.append("| Metric | Value |")
    L.append("|---|---|")
    for label, key in [("Total tasks", "total_tasks"), ("Completed", "completed_tasks"), ("Overdue", "overdue_tasks"),
                       ("Blocked (status)", "blocked_tasks"), ("Waiting (status)", "waiting_tasks"),
                       ("At risk (projected late or status At Risk)", "tasks_at_risk"), ("Due within 2 workdays", "approaching_due"),
                       ("Stale (no owner update)", "stale_tasks"), ("Unanswered follow-ups", "unanswered_followups"),
                       ("Pending approvals", "pending_approvals"), ("Open decisions", "open_decisions")]:
        L.append(f"| {label} | {m[key]} |")
    L.append("")
    L.append("**Upcoming milestones**")
    L.append("")
    L.append("| Milestone | Due | Projected | Slack (workdays) |")
    L.append("|---|---|---|---|")
    for ms in m["upcoming_milestones"]:
        L.append(f"| {ms['name']} | {ms['due_date']} | {ms['projected_finish']} | {ms['slack_workdays']} |")
    L.append("")
    L.append("## Most important blockers")
    L.append("")
    if not bn and not h["projected_late"]:
        L.append("No task is currently holding up other work.")
    for b in bn:
        t = tasks[b["task_id"]]
        L.append(f"- **{b['task_id']} {b['name']}** ({_name(store, b['owner_id'])}, {t['status']}, due {t['due_date']}) blocks "
                 + ", ".join(f"{d} {tasks[d]['name']} ({_name(store, tasks[d]['owner_id'])})" for d in b["blocking"])
                 + f". {b['downstream_total']} task(s) downstream in total.")
        cms = store.list_commitments(b["task_id"], current_only=True)
        if cms:
            L.append(f"  - Latest commitment: {describe(cms[-1])}.")
        blockers = [x for x in store.list_blockers() if x["task_id"] == b["task_id"]]
        for x in blockers:
            L.append(f"  - Reported blocker: {x['description']} (source {x['source_message_id']}).")
    for p in h["projected_late"]:
        L.append(f"- {p['task_id']} {p['name']} projects to {p['projected_finish']}, {-p['slack_workdays']} workday(s) after its "
                 f"due date {p['due_date']} (basis: {p['basis']}).")
    L.append("")
    L.append("## Latest updates")
    L.append("")
    since = now - timedelta(days=2)
    rows = []
    for c in store.list_changes():
        rows.append((c["applied_at"], f"Board change on {c['task_id']} (approved {c['approval_id']}): {c['before']} -> {c['after']}"))
    for u in store.list_updates():
        if parse_dt(u["created_at"]) >= since:
            rows.append((u["created_at"], f"Monday update {u['update_id']} on {u['task_id']} by {_name(store, u['author_id'])}: "
                                          f"{u['body'][:160]}"))
    for e in store.list_processed_events():
        r = e["result"]
        if r.get("status") == "processed":
            i = r["interpretation"]
            rows.append((e["processed_at"], f"Reply {r['message_id']} from {_name(store, r['sender_id'])} on {i['related_task_id']}: "
                                            f"{i['message_intent']} - {i['summary']}"))
    for f in store.list_followups():
        if f["sent_at"]:
            rows.append((f["sent_at"], f"Follow-up {f['followup_id']} sent (mock {f['channel']}) to {_name(store, f['recipient_id'])} "
                                       f"about {f['task_id']}; reply: {f['reply_status']}"))
    for msg in store.list_slack_messages():
        if msg["direction"] == "history" and parse_dt(msg["ts"]) >= since and msg["task_refs"]:
            author = store.member_by_slack_id(msg["author_slack_id"])
            if author:
                rows.append((msg["ts"], f"Slack {msg['message_id']} from {author['name']}: \"{msg['text'][:140]}\""))
    for em in store.list_email_messages():
        if parse_dt(em["sent_at"]) >= since:
            rows.append((em["sent_at"], f"Email {em['message_id']} from {em['from_addr']}: \"{em['subject']}\""))
    rows.sort(key=lambda r: parse_dt(r[0]))
    for at, text in rows[-14:]:
        L.append(f"- `{parse_dt(at).strftime('%b %d %H:%M')}` {text}")
    if not rows:
        L.append("No updates in the last two days.")
    L.append("")
    L.append("## Pending decisions")
    L.append("")
    if not h["pending_approvals"] and not h["decisions"] and not h["conflicts"]:
        L.append("Nothing awaiting a decision.")
    for a in h["pending_approvals"]:
        L.append(f"- **Approval {a['approval_id']}** ({a['action_type']}) - {a['target']}: {a['reason']}")
    for d in h["decisions"]:
        L.append(f"- **Decision {d['decision_id']}** for {_name(store, d['owner_id'])}: {d['question']} (evidence: {', '.join(d['evidence'])})")
    for c in h["conflicts"]:
        L.append(f"- **Conflict** on {c['task_id']}: board says {c['board_due_date']}, {c['source']} says {c['reported_date']}"
                 + (f" (proposal {c['pending_proposal']} pending)" if c["pending_proposal"] else " (no proposal yet)") + ".")
    L.append("")
    L.append("## Recommended next steps")
    L.append("")
    steps = []
    for a in h["pending_approvals"]:
        steps.append(f"Review and decide approval {a['approval_id']}: `python run.py approve --request {a['approval_id']}`.")
    for d in h["decisions"]:
        steps.append(f"Project lead to decide {d['decision_id']}: {d['question']}")
    for p in select_followups(store, policy):
        if p["recommendation"] == "follow_up":
            steps.append(f"Follow up with {_name(store, p['owner_id'])} on {p['task_id']} ({p['name']}): {p['why']}")
        elif p["recommendation"] == "escalate":
            steps.append(f"Escalate {p['task_id']} to the project lead: {p['why']}")
        elif p["recommendation"] == "wait":
            steps.append(f"Wait on {p['task_id']} ({_name(store, p['owner_id'])}): {p['why']}")
    for f in h["unanswered_followups"]:
        steps.append(f"Follow-up {f['followup_id']} is unanswered; the next reminder is allowed after {f['next_allowed_at']}.")
    for i, s in enumerate(steps[:10], 1):
        L.append(f"{i}. {s}")
    if not steps:
        L.append("No action needed right now.")
    L.append("")
    L.append("## Dependency graph")
    L.append("")
    L.append("```mermaid")
    L.append(DependencyGraph.from_store(store).mermaid(tasks, {b["task_id"] for b in bn} | {o["task_id"] for o in h["overdue"]}))
    L.append("```")
    L.append("")
    L.append("## Source references")
    L.append("")
    L.append("| Task | Name | Owner | Status | Due | Projected | Basis | Latest owner signal |")
    L.append("|---|---|---|---|---|---|---|---|")
    from ..monitoring.overdue_tasks import last_owner_update
    for tid, t in tasks.items():
        p = h["projections"][tid]
        last = last_owner_update(store, tid)
        sig = f"{last['id']} ({parse_dt(last['at']).strftime('%b %d %H:%M')})" if last else "-"
        L.append(f"| {tid} | {t['name']} | {_name(store, t['owner_id'])} | {t['status']} | {t['due_date']} | "
                 f"{p['projected_finish']} | {p['basis']} | {sig} |")
    L.append("")
    hist = store.list_health()
    if len(hist) > 1:
        L.append("**Health history:** " + " -> ".join(f"{parse_dt(x['created_at']).strftime('%b %d %H:%M')} {x['classification']}"
                                                       for x in hist[-6:]))
        L.append("")
    L.append(f"*Method: {h['method_note']} Projections use working days from config/policy.json. Estimates are labeled; "
             "nothing in this report was marked complete without a board record.*")
    return "\n".join(L) + "\n", h


def write_briefing(store: Any, policy: dict[str, Any], reports_dir: Path, *, charts: bool = False) -> dict[str, Any]:
    text, h = build_briefing(store, policy)
    reports_dir.mkdir(parents=True, exist_ok=True)
    stamp = store.now().strftime("%Y%m%d-%H%M")
    path = reports_dir / f"project-health-{stamp}.md"
    chart_paths: list[str] = []
    chart_note = None
    if charts:
        from .charts import render_charts
        chart_paths, chart_note = render_charts(h, reports_dir / "charts", stamp)
        if chart_paths:
            text += "\n## Charts\n\n" + "\n".join(f"![{Path(c).stem}](charts/{Path(c).name})" for c in chart_paths) + "\n"
    path.write_text(text, encoding="utf-8")
    (reports_dir / "latest.md").write_text(text, encoding="utf-8")
    return {"path": str(path), "latest": str(reports_dir / "latest.md"), "classification": h["classification"],
            "charts": chart_paths, "chart_note": chart_note}
