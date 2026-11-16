"""Overdue, approaching, stale and conflicting task detection - plus per-task activity history."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from ..clock import BusinessCalendar, find_date_mentions, parse_dt


def overdue_tasks(tasks: list[dict[str, Any]], today: date) -> list[dict[str, Any]]:
    out = []
    for t in tasks:
        if t["status"] != "Done" and date.fromisoformat(t["due_date"]) < today:
            out.append({**t, "days_overdue": (today - date.fromisoformat(t["due_date"])).days})
    return out


def approaching_tasks(tasks: list[dict[str, Any]], today: date, cal: BusinessCalendar, within_workdays: int) -> list[dict[str, Any]]:
    out = []
    for t in tasks:
        due = date.fromisoformat(t["due_date"])
        if t["status"] != "Done" and due >= today and cal.workdays_between(today, due) <= within_workdays:
            out.append(t)
    return out


def task_activity(store: Any, task_id: str) -> list[dict[str, Any]]:
    """Everything we know happened on a task, newest last, with provenance. Text is untrusted content."""
    task = store.get_task(task_id)
    if task is None:
        raise KeyError(task_id)
    owner = store.get_member(task["owner_id"]) or {}
    items: list[dict[str, Any]] = []
    for u in store.list_updates(task_id):
        items.append({"source": "monday_update", "id": u["update_id"], "at": u["created_at"], "author_id": u["author_id"],
                      "text": u["body"], "from_owner": u["author_id"] == task["owner_id"]})
    followup_threads = {f["thread_id"]: f for f in store.list_followups(task_id=task_id) if f.get("thread_id")}
    for m in store.list_slack_messages():
        in_thread = m["thread_id"] in followup_threads
        if task_id in m["task_refs"] or in_thread:
            author = store.member_by_slack_id(m["author_slack_id"])
            items.append({"source": "slack", "id": m["message_id"], "at": m["ts"], "channel_id": m["channel_id"],
                          "thread_id": m["thread_id"], "direction": m["direction"],
                          "author_id": author["member_id"] if author else m["author_slack_id"],
                          "text": m["text"], "from_owner": bool(author and author["member_id"] == task["owner_id"])})
    for th in store.list_email_threads():
        if task_id in th.get("related_task_ids", []):
            for em in store.list_email_messages(th["thread_id"]):
                items.append({"source": "email", "id": em["message_id"], "thread_id": th["thread_id"], "at": em["sent_at"],
                              "author_id": em["from_addr"], "text": em["body"], "subject": em["subject"],
                              "direction": em["direction"],
                              "from_owner": em["from_addr"].lower() == owner.get("email", "").lower()})
    items.sort(key=lambda i: parse_dt(i["at"]))
    return items


def last_owner_update(store: Any, task_id: str) -> dict[str, Any] | None:
    owner_items = [i for i in task_activity(store, task_id) if i["from_owner"]]
    return owner_items[-1] if owner_items else None


def stale_tasks(store: Any, now: datetime, stale_days: int) -> list[dict[str, Any]]:
    out = []
    for t in store.list_tasks():
        if t["status"] in {"Done", "Not Started"}:
            continue
        last = last_owner_update(store, t["task_id"])
        last_at = parse_dt(last["at"]) if last else None
        if last_at is None or now - last_at > timedelta(days=stale_days):
            out.append({"task_id": t["task_id"], "name": t["name"], "owner_id": t["owner_id"],
                        "last_owner_update_at": last["at"] if last else None,
                        "days_since_update": (now - last_at).days if last_at else None})
    return out


def date_conflicts(store: Any) -> list[dict[str, Any]]:
    """Places where what people said disagrees with the board's due date.

    Two sources: (1) estimates the agent extracted from replies (agent_commitments) and
    (2) owner-authored messages that state a committed date ("planning on the 18th") newer
    than the latest board update. Conflicts are reported, never auto-resolved.
    """
    out = []
    proposals = store.list_proposals()
    for t in store.list_tasks():
        if t["status"] == "Done":
            continue
        pending = [p for p in proposals if p["task_id"] == t["task_id"] and p["status"] in {"pending_approval", "approved"}
                   and "due_date" in p["changes"]]
        for c in store.list_commitments(t["task_id"], current_only=True):
            if c["commitment_date"] and c["commitment_date"] != t["due_date"] and c["kind"] in {"estimate", "confirmed_date"}:
                out.append({"task_id": t["task_id"], "board_due_date": t["due_date"], "reported_date": c["commitment_date"],
                            "source": c["source_message_id"], "kind": "owner_estimate",
                            "pending_proposal": pending[0]["proposal_id"] if pending else None})
        board_updates = [u for u in store.list_updates(t["task_id"])]
        last_board = max((parse_dt(u["created_at"]) for u in board_updates), default=None)
        for item in task_activity(store, t["task_id"]):
            if item["source"] != "slack" or not item["from_owner"] or item.get("direction") == "incoming":
                continue
            if last_board and parse_dt(item["at"]) <= last_board:
                continue
            for mention in find_date_mentions(item["text"], parse_dt(item["at"])):
                if mention["commitment_cue"] and not mention["negated"] and mention["date"] != t["due_date"]:
                    if not any(o["task_id"] == t["task_id"] and o["reported_date"] == mention["date"] for o in out):
                        out.append({"task_id": t["task_id"], "board_due_date": t["due_date"], "reported_date": mention["date"],
                                    "source": item["id"], "kind": "message_mention_unverified",
                                    "pending_proposal": pending[0]["proposal_id"] if pending else None})
    return out
