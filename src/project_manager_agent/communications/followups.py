"""The follow-up decision workflow: who to ask, whether we may ask, and the record of asking.

Python decides *whether* a follow-up is allowed (rules, history, frequency). The
model (or the offline templates) decides *what to say*. Sending always needs a human.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any

from ..approvals.requests import create_request
from ..clock import BusinessCalendar, fmt_day, parse_dt
from ..dependencies.graph import DependencyGraph
from ..dependencies.scheduling import current_estimates, project_schedule
from ..integrations.mock_gmail import MockGmail
from ..integrations.mock_slack import MockSlack, RecipientError
from ..monitoring.overdue_tasks import last_owner_update, task_activity
from ..monitoring.priorities import rank_attention
from ..storage import Store, new_id
from .message_drafting import draft_stale_check, draft_status_check, lint_message

PURPOSES = {"status_check", "stale_check", "downstream_notice", "clarification", "escalation"}


class FollowupRefused(ValueError):
    def __init__(self, reasons: list[str]):
        self.reasons = reasons
        super().__init__("; ".join(reasons))


def _exempt(store: Store, target_type: str, target_id: str, now: datetime) -> dict[str, Any] | None:
    for e in store.list_exemptions():
        if e["target_type"] == target_type and e["target_id"] == target_id and parse_dt(e["until"]) > now:
            return e
    return None


def evaluate_followup(store: Store, policy: dict[str, Any], task_id: str, recipient_id: str,
                      purpose: str = "status_check") -> dict[str, Any]:
    """Run every follow-up rule and return a decision with the evidence for each check."""
    cal = BusinessCalendar(policy)
    fp = policy["followups"]
    now = store.now()
    checks: list[dict[str, Any]] = []
    action = "followup"

    def check(name: str, ok: bool, detail: str, on_fail: str = "skip") -> None:
        nonlocal action
        checks.append({"check": name, "passed": ok, "detail": detail})
        if not ok and action == "followup":
            action = on_fail

    task = store.get_task(task_id)
    member = store.get_member(recipient_id)
    contact = store.get_contact(recipient_id)
    check("task_exists", task is not None, f"{task_id} {'found' if task else 'not found'}")
    if task is None:
        return {"allowed": False, "action": "skip", "checks": checks, "task_id": task_id, "recipient_id": recipient_id}
    check("recipient_verified", bool((member and member.get("verified") and member.get("on_project"))
                                     or (contact and contact.get("approved_contact"))),
          f"{recipient_id}: {'verified project member' if member and member.get('on_project') else 'approved contact' if contact else 'not a verified project member or approved contact'}")
    check("task_open", task["status"] != "Done", f"status is {task['status']}")
    ex = _exempt(store, "member", recipient_id, now) or _exempt(store, "task", task_id, now)
    check("not_exempt", ex is None, f"exempt until {ex['until']}: {ex['reason']}" if ex else "no active exemption")

    qkey = f"{purpose}:{task_id}"
    mine = store.list_followups(task_id=task_id, recipient_id=recipient_id)
    queued = [f for f in mine if f["question_key"] == qkey and f["status"] in {"drafted", "pending_approval", "approved"}]
    check("no_duplicate_queued", not queued,
          f"already queued as {queued[0]['followup_id']} ({queued[0]['status']})" if queued else "nothing queued for this question")

    awaiting = [f for f in mine if f["status"] == "sent" and f["reply_status"] == "awaiting"]
    if awaiting:
        last = awaiting[-1]
        reply_due = cal.add_working_hours(parse_dt(last["sent_at"]), fp["reply_wait_working_hours"])
        check("not_awaiting_reply", now >= reply_due,
              f"{last['followup_id']} sent {last['sent_at']}; waiting for a reply until {reply_due.isoformat()}", on_fail="wait")
    sent = [f for f in mine if f["sent_at"]]
    if sent:
        nxt = max(parse_dt(f["next_allowed_at"]) for f in sent if f["next_allowed_at"]) if any(f["next_allowed_at"] for f in sent) else None
        if nxt:
            check("min_interval", now >= nxt, f"next follow-up allowed after {nxt.isoformat()} "
                  f"({fp['min_working_hours_between_followups']} working hours between follow-ups)", on_fail="wait")
    replied_recent = [f for f in mine if f["last_reply_at"] and now - parse_dt(f["last_reply_at"]) < timedelta(hours=fp["fresh_update_hours"])]
    if purpose in {"status_check", "stale_check"}:
        check("no_recent_reply", not replied_recent,
              f"{recipient_id} replied at {replied_recent[-1]['last_reply_at']}" if replied_recent else "no recent reply on file")
        last = last_owner_update(store, task_id) if recipient_id == task["owner_id"] else None
        fresh = bool(last and (now - parse_dt(last["at"])) <= timedelta(hours=fp["fresh_update_hours"]))
        check("no_fresh_owner_update", not fresh,
              f"owner updated {last['at']} via {last['id']}" if fresh and last else
              (f"last owner update {last['at']} ({last['id']}) is older than {fp['fresh_update_hours']}h" if last else "no owner update on file"))
    today_sent = [f for f in store.list_followups(recipient_id=recipient_id)
                  if f["sent_at"] and parse_dt(f["sent_at"]).date() == now.date()]
    check("daily_cap", len(today_sent) < fp["max_followups_per_person_per_day"],
          f"{len(today_sent)} follow-up(s) already sent to {recipient_id} today (cap {fp['max_followups_per_person_per_day']})", on_fail="wait")
    unanswered_attempts = [f for f in mine if f["question_key"] == qkey and f["status"] == "sent" and f["reply_status"] == "awaiting"]
    if len(unanswered_attempts) >= fp["max_attempts_before_escalation"] and action in {"followup", "wait"}:
        checks.append({"check": "attempt_limit", "passed": False,
                       "detail": f"{len(unanswered_attempts)} unanswered attempt(s); escalate to the project lead instead"})
        action = "escalate"
    if not cal.is_working_time(now):
        checks.append({"check": "working_hours", "passed": True,
                       "detail": f"outside working hours; drafting allowed, sending deferred to {cal.next_working_time(now).isoformat()}"})
    return {"allowed": action == "followup", "action": action, "checks": checks, "task_id": task_id,
            "recipient_id": recipient_id, "purpose": purpose, "question_key": qkey,
            "attempt": len([f for f in mine if f["question_key"] == qkey and f["sent_at"]]) + 1}


def choose_channel(store: Store, policy: dict[str, Any], recipient_id: str, topic: str | None = None) -> str:
    if store.get_contact(recipient_id):
        return policy["channels"]["external_default"]
    if topic and topic in policy["channels"]["formal_topics"]:
        return "email"
    member = store.get_member(recipient_id) or {}
    return member.get("preferred_channel") or policy["channels"]["internal_default"]


def known_context_for(store: Store, task: dict[str, Any]) -> str | None:
    """Offline helper: has a non-owner already told us *something* since the owner's last update?"""
    last = last_owner_update(store, task["task_id"])
    since = parse_dt(last["at"]) if last else None
    for item in reversed(task_activity(store, task["task_id"])):
        if since and parse_dt(item["at"]) <= since:
            break
        if not item["from_owner"] and re.search(r"reschedul|postpon|delay|could not complete|couldn't", item["text"], re.I):
            sender = item["author_id"]
            org = next((c["organization"].split(" (")[0] for c in store.list_contacts() if c["email"] == sender), None)
            who = org or (store.get_member(sender) or {}).get("first_name") or "someone"
            return f"I saw the note from {who} about rescheduling."
    return None


def select_followups(store: Store, policy: dict[str, Any]) -> list[dict[str, Any]]:
    """Rank tasks needing attention and decide, for each, whether and whom to ask."""
    cal = BusinessCalendar(policy)
    graph = DependencyGraph.from_store(store)
    tasks = {t["task_id"]: t for t in store.list_tasks()}
    proj = project_schedule(graph, tasks, store.now().date(), cal, current_estimates(store))
    out = []
    stale_days = policy["followups"]["stale_update_days"]
    from ..monitoring.overdue_tasks import stale_tasks
    stale_ids = {s["task_id"] for s in stale_tasks(store, store.now(), stale_days)}
    ranked = rank_attention(store, cal, policy, proj, graph)
    ranked_ids = {r["task_id"] for r in ranked}
    for sid in sorted(stale_ids - ranked_ids):  # stale but otherwise fine: low-priority status check
        t = tasks[sid]
        ranked.append({"task_id": sid, "name": t["name"], "owner_id": t["owner_id"], "status": t["status"],
                       "due_date": t["due_date"], "score": 1.0, "components": {"stale_update": 1.0},
                       "covered_by_upstream": None, "fresh_update": None, "downstream_open": [], "direct_dependents": []})
    for item in ranked:
        purpose = "stale_check" if item["task_id"] in stale_ids and not item.get("overdue") else "status_check"
        if item["covered_by_upstream"]:
            up = tasks[item["covered_by_upstream"]]
            up_owner = (store.get_member(up["owner_id"]) or {}).get("first_name", up["owner_id"])
            has_estimate = any(c["task_id"] == up["task_id"] and c["commitment_date"]
                               for c in store.list_commitments(current_only=True))
            why = (f"Its dates depend on {up['task_id']} ({up['name']}), which now has an owner estimate; any date change "
                   "is a project-lead decision, not a reason to chase this owner." if has_estimate else
                   f"Waiting on {up['task_id']} ({up['name']}). Ask {up_owner} about the shared blocker first "
                   "instead of messaging this owner separately.")
            out.append({**item, "recommendation": "no_contact", "why": why})
            continue
        decision = evaluate_followup(store, policy, item["task_id"], item["owner_id"], purpose)
        if item.get("fresh_update"):
            rec, why = "no_contact", f"Fresh owner update {item['fresh_update']['id']} at {item['fresh_update']['at']} already answers it."
        elif decision["action"] == "followup":
            rec, why = "follow_up", "Needs an update and the follow-up rules allow one now."
        elif decision["action"] == "escalate":
            rec, why = "escalate", "Unanswered follow-ups reached the attempt limit; escalate to the project lead."
        else:
            failed = [c for c in decision["checks"] if not c["passed"]]
            why = "; ".join(c["detail"] for c in failed) or decision["action"]
            rec = "already_queued" if any(c["check"] == "no_duplicate_queued" for c in failed) else \
                ("wait" if decision["action"] == "wait" else "no_contact")
        out.append({**item, "recommendation": rec, "why": why, "purpose": purpose,
                    "channel": choose_channel(store, policy, item["owner_id"]), "decision": decision})
    return out


def create_followup_draft(store: Store, policy: dict[str, Any], *, task_id: str, recipient_id: str, channel: str,
                          message: str, purpose: str, reason: str, evidence: list[str] | None = None,
                          subject: str | None = None, created_by: str = "pm-agent", thread_id: str | None = None,
                          question_key: str | None = None) -> dict[str, Any]:
    """Validate, dedupe and record a draft, then open an approval request for sending it."""
    if purpose not in PURPOSES:
        raise FollowupRefused([f"Unknown purpose {purpose!r}"])
    if channel not in {"slack", "email"}:
        raise FollowupRefused([f"Unknown channel {channel!r}"])
    task = store.get_task(task_id)
    if task is None:
        raise FollowupRefused([f"Unknown task {task_id}"])
    qkey = question_key or f"{purpose}:{task_id}"
    existing = [f for f in store.list_followups(task_id=task_id, recipient_id=recipient_id)
                if f["question_key"] == qkey and f["status"] in {"drafted", "pending_approval", "approved"}]
    if existing:
        return {**existing[-1], "deduplicated": True}

    if purpose in {"status_check", "stale_check"}:
        decision = evaluate_followup(store, policy, task_id, recipient_id, purpose)
        if not decision["allowed"]:
            raise FollowupRefused([c["detail"] for c in decision["checks"] if not c["passed"]] or [decision["action"]])

    slack, gmail = MockSlack(store), MockGmail(store)
    try:
        if channel == "slack":
            recipient = slack.validate_recipient(recipient_id)
            to_addr = None
        else:
            to_addr, _ = gmail.resolve_address(recipient_id)
            recipient = store.get_member(recipient_id) or store.get_contact(recipient_id) or {}
    except RecipientError as exc:
        raise FollowupRefused([str(exc)]) from exc
    errors = lint_message(message, recipient, policy)
    if channel == "email" and not (subject or "").strip():
        errors.append("Email drafts need a subject.")
    if errors:
        raise FollowupRefused(errors)

    fid = new_id("FU")
    attempt = len([f for f in store.list_followups(task_id=task_id, recipient_id=recipient_id)
                   if f["question_key"] == qkey and f["sent_at"]]) + 1
    if channel == "slack":
        payload = {"followup_id": fid, "task_id": task_id, "recipient_id": recipient_id,
                   "channel_id": slack.dm_channel_for(recipient), "text": message, "thread_id": thread_id}
        action_type, target = "send_slack_message", f"Slack DM to {recipient.get('name')} ({recipient_id})"
    else:
        payload = {"followup_id": fid, "task_id": task_id, "recipient_id": recipient_id, "to": to_addr,
                   "subject": subject, "body": message, "thread_id": thread_id}
        action_type, target = "send_email", f"Email to {recipient.get('name')} <{to_addr}>"
    store.insert_followup({"followup_id": fid, "task_id": task_id, "recipient_id": recipient_id, "channel": channel,
                           "purpose": purpose, "question_key": qkey, "subject": subject, "draft": message, "reason": reason,
                           "evidence": evidence or [], "status": "drafted", "attempt": attempt,
                           "created_at": store.now().isoformat(), "created_by": created_by})
    ap = create_request(store, action_type=action_type, payload=payload, target=target, reason=reason,
                        evidence=evidence, requested_by=created_by)
    store.update_followup(fid, approval_id=ap["approval_id"], status="pending_approval")
    return {**store.get_followup(fid), "deduplicated": False}  # type: ignore[dict-item]


def offline_draft_for(store: Store, item: dict[str, Any]) -> str:
    """Template message used by the offline rules engine (the model writes its own)."""
    task = store.get_task(item["task_id"])
    if item.get("purpose") == "stale_check":
        return draft_stale_check(store, task)
    graph = DependencyGraph.from_store(store)
    return draft_status_check(store, task, graph.direct_dependents(task["task_id"]),
                              known_context=known_context_for(store, task), today=store.now().date())


def describe_followup(store: Store, f: dict[str, Any]) -> str:
    member = store.get_member(f["recipient_id"]) or store.get_contact(f["recipient_id"]) or {}
    when = f" sent {fmt_day(parse_dt(f['sent_at']).date())}" if f.get("sent_at") else ""
    return f"{f['followup_id']} [{f['status']}{when}] {f['channel']} -> {member.get('name', f['recipient_id'])} about {f['task_id']}"
