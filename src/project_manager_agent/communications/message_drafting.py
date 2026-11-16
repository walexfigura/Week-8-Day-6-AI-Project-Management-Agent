"""Message templates (offline mode) and a linter that every draft must pass - model-written or not."""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from ..clock import fmt_day


def short_name(task: dict[str, Any]) -> str:
    """Plain-words name for messages ("the product photos"), from the task's short_name field."""
    return task.get("short_name") or ("the " + task["name"][0].lower() + task["name"][1:])


def _people_waiting(store: Any, task: dict[str, Any], dependents: list[str]) -> str:
    parts = []
    for tid in dependents:
        t = store.get_task(tid)
        if not t or t["status"] == "Done" or t["owner_id"] in {task["owner_id"], "TM-ALEX"}:
            continue
        owner = store.get_member(t["owner_id"]) or {}
        thing = short_name(t)
        thing = thing[4:] if thing.startswith("the ") else thing
        parts.append(f"{owner.get('first_name', 'the team')}'s {thing}")
    if not parts:
        return ""
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]


def draft_status_check(store: Any, task: dict[str, Any], dependents: list[str], *, known_context: str | None,
                       today: date) -> str:
    owner = store.get_member(task["owner_id"]) or {"first_name": "there"}
    waiting = _people_waiting(store, task, dependents)
    due = date.fromisoformat(task["due_date"])
    text = f"Hey {owner['first_name']}! Checking in on {short_name(task)}."
    if waiting:
        text += f" {waiting[0].upper() + waiting[1:]} {'are' if ' and ' in waiting else 'is'} waiting on them."
    if known_context:
        text += f" {known_context} Do you have a new date for them?"
    elif due < today:
        text += " Are we still expecting them today, or has the timeline changed?"
    else:
        text += f" Are we still on track for {fmt_day(due)}?"
    return text


def draft_stale_check(store: Any, task: dict[str, Any]) -> str:
    owner = store.get_member(task["owner_id"]) or {"first_name": "there"}
    return (f"Hey {owner['first_name']}! Quick check on {short_name(task)} - I haven't seen an update in a while. "
            f"Still on track for {fmt_day(date.fromisoformat(task['due_date']))}, or is anything holding it up?")


def draft_downstream_notice(store: Any, recipient_task: dict[str, Any], upstream_task: dict[str, Any], new_date: date,
                            reason: str | None, revised_finish: str, would_miss: bool) -> str:
    owner = store.get_member(recipient_task["owner_id"]) or {"first_name": "there"}
    up_owner = store.get_member(upstream_task["owner_id"]) or {"first_name": "the owner"}
    why = f" ({reason.rstrip('.').lower()})" if reason else ""
    text = (f"Heads up {owner['first_name']} - {up_owner['first_name']}'s final {short_name(upstream_task).replace('the ', '')} "
            f"are now expected {fmt_day(new_date)}{why}. That's an estimate, not confirmed yet.")
    due = date.fromisoformat(recipient_task["due_date"])
    if would_miss:
        text += (f" On that timeline, {short_name(recipient_task)} would land around {fmt_day(date.fromisoformat(revised_finish))}, "
                 f"after the {fmt_day(due)} due date. Can you still make {fmt_day(due)}, or should we plan for a later date?")
    else:
        text += f" Can {short_name(recipient_task)} still hit {fmt_day(due)} if the images land then?"
    return text


CLARIFICATIONS = {
    "blocker_report": "Thanks {name}. What's it waiting on exactly, and do you have a rough date for {thing}?",
    "ambiguous": "Thanks {name}! Just to confirm: are {thing} delivered, or still in progress? If still in progress, what date should we plan around?",
    "deadline_change_request": "Thanks {name}, I'll flag that to Alex. What specific date would work for {thing}, and what's driving the change?",
    "delivery_claim": "Thanks {name}! Where did {thing} go (link or who received them)? I don't see them on the task yet, so I want to confirm before updating anything.",
    "ownership_redirect": "Got it, thanks {name}. Just so I update the right thing: is {other} now handling {thing} entirely, or just part of it?",
    "completion_claim": "Great, thanks {name}! Who's doing the review, and when do you expect it to be signed off?",
    "no_task": "Thanks {name}! Which task is this about? I want to make sure I attach it to the right one.",
}


def draft_clarification(store: Any, task: dict[str, Any] | None, member: dict[str, Any], intent: str, other: str | None = None) -> str:
    template = CLARIFICATIONS.get(intent, CLARIFICATIONS["ambiguous"])
    thing = short_name(task) if task else "this"
    return template.format(name=member.get("first_name", "there"), thing=thing, other=other or "they")


class DraftRejected(ValueError):
    pass


_COMPLETION_CLAIM = re.compile(r"\b(is|are|has been|have been|was|were)\s+(now\s+)?(done|complete|completed|finished|delivered)\b", re.I)


def lint_message(text: str, recipient: dict[str, Any], policy: dict[str, Any]) -> list[str]:
    """Return hard errors. Drafts with errors are rejected before an approval is ever requested."""
    errors = []
    rules = policy["messages"]
    if not text.strip():
        errors.append("Message is empty.")
    if len(text) > rules["max_length_chars"]:
        errors.append(f"Message is {len(text)} characters; keep it under {rules['max_length_chars']}.")
    lowered = text.lower()
    for phrase in rules["banned_phrases"]:
        if phrase in lowered:
            errors.append(f"Avoid the phrase {phrase!r} (see knowledge-base/communication-guidelines.md).")
    first = recipient.get("first_name") or recipient.get("name", "").split(" ")[0]
    if first and first.lower() not in lowered[:80]:
        errors.append(f"Address the recipient by name ({first}) near the start of the message.")
    if re.search(r"\b(sent|messaged|updated the board|changed the due date)\b.*\b(already|just)\b", lowered):
        errors.append("Do not claim actions were taken; drafts describe questions, not completed actions.")
    return errors
