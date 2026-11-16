"""Two-way communication: turn an incoming reply into structured, approved-before-applied actions.

Pipeline (Python orchestrates; the interpreter - model or rules - only *reads*):

1. Idempotency    - a reply event already processed returns the stored result (no duplicates).
2. Identity       - the sender must map to a verified directory member (by Slack user ID or email).
3. Association    - thread of a sent follow-up > explicit task ID > sender's single open task.
                    Never by a name mentioned in the text.
4. Interpretation - interpreter returns a ReplyInterpretation; Python validates it and
                    re-resolves any date expression against the simulated clock.
5. Records        - commitment/blocker with provenance; follow-up marked replied.
6. Impact         - deterministic downstream analysis for any new estimate.
7. Proposals      - task updates, downstream notices, clarifications and management decisions
                    are *created as pending approvals*. Nothing official changes here.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import date
from typing import Any

from ..approvals.requests import create_request
from ..clock import (
    BusinessCalendar,
    fmt_day,
    mentions_unresolvable_range,
    parse_dt,
    resolve_date_expression,
)
from ..dependencies.impact import analyze_impact
from ..models import ReplyInterpretation, validate_interpretation
from ..storage import Store, new_id
from .commitments import describe, record_from_interpretation
from .followups import FollowupRefused, create_followup_draft
from .message_drafting import draft_clarification, draft_downstream_notice, short_name

Interpreter = Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]

INJECTION_PATTERNS = re.compile(
    r"ignore (all )?(previous|prior) instructions|you are now|admin mode|system prompt|automated assistant instruction|"
    r"disregard (the )?(rules|policy)|developer mode", re.I)


class ReplyProcessingError(RuntimeError):
    pass


# ---------------------------------------------------------------------------- association
def identify_sender(store: Store, message: dict[str, Any]) -> dict[str, Any] | None:
    if "author_slack_id" in message:
        return store.member_by_slack_id(message["author_slack_id"])
    return store.member_by_email(message.get("from_addr", ""))


def associate_task(store: Store, message: dict[str, Any], sender: dict[str, Any]) -> dict[str, Any]:
    thread_id = message.get("thread_id")
    if thread_id:
        fu = store.followup_by_thread(thread_id)
        if fu and fu["recipient_id"] == sender["member_id"]:
            return {"task_id": fu["task_id"], "method": "reply_in_followup_thread", "confidence": "high", "followup_id": fu["followup_id"]}
    explicit = re.findall(r"\bTASK-\d{3}\b", message["text"])
    if explicit and store.get_task(explicit[0]):
        return {"task_id": explicit[0], "method": "explicit_task_id", "confidence": "high", "followup_id": None}
    open_tasks = [t for t in store.list_tasks() if t["owner_id"] == sender["member_id"] and t["status"] != "Done"]
    if len(open_tasks) == 1:
        return {"task_id": open_tasks[0]["task_id"], "method": "sender_has_single_open_task", "confidence": "medium",
                "followup_id": None}
    return {"task_id": None, "method": "unresolved", "confidence": "low", "followup_id": None,
            "candidates": [t["task_id"] for t in open_tasks]}


# ---------------------------------------------------------------------------- rules interpreter
def rules_interpreter(message: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    """Conservative keyword rules used offline. Handles the documented scenarios; flags everything
    else as ambiguous rather than guessing. The model interpreter handles messier language."""
    text = message["text"]
    low = text.lower()
    ref = parse_dt(message.get("ts") or message.get("sent_at"))
    when, expr = resolve_date_expression(text, ref)
    task = context.get("task") or {}
    interp = ReplyInterpretation(related_task_id=task.get("task_id"), message_intent="ambiguous", interpreter="rules")
    interp.possible_prompt_injection = bool(INJECTION_PATTERNS.search(text))
    blocker = None
    m = re.search(r"(photographer|studio|vendor|supplier|design|[a-z]+)\s+(had to |needs to |has to )?(reschedul\w*|delay\w*|postpon\w*|is late|is sick|cancel\w*)", low)
    if m:
        blocker = m.group(0).capitalize()
    other_people = [p for p in context.get("project_people", []) if p["first_name"].lower() in low
                    and p["member_id"] != context.get("sender", {}).get("member_id")]

    if re.search(r"\bask (\w+)|\b(\w+) is handling|\bhe'?s handling|\bshe'?s handling|\bthey'?re handling", low):
        interp.message_intent = "ownership_redirect"
        interp.mentioned_people = [p["member_id"] for p in other_people]
        interp.requires_clarification = True
        interp.summary = "Sender says someone else is handling the work. Ownership change is unverified."
        interp.uncertainties.append("Not confirmed by the named person or the project lead.")
    elif re.search(r"\b(push|move|extend|delay)\b.*\b(it|this|deadline|date)\b|\bmore time\b", low) and "?" in text:
        interp.message_intent = "deadline_change_request"
        interp.requires_clarification = when is None
        interp.new_estimated_completion = when.isoformat() if when else None
        interp.date_expression = expr
        interp.summary = "Sender asks to move the deadline. That is a project-lead decision."
        if mentions_unresolvable_range(text):
            interp.uncertainties.append("'next week' is a range, not a date; no date was invented.")
    elif re.search(r"\b(done|finished|complete[d]?)\b", low) and not re.search(r"(should|will|'ll|\bbe|\bby|until|have)\b.{0,30}\b(done|finished|complete)", low):
        interp.message_intent = "completion_claim"
        interp.completion_confirmed = False
        interp.requires_task_update = True
        interp.requires_clarification = "review" in low or "almost" in low
        interp.summary = "Sender reports the work is finished" + (" but still needs review." if "review" in low else ".")
        interp.uncertainties.append("Completion not verified against the Done definition.")
    elif re.search(r"\b(sent|delivered|uploaded|shared)\b", low) and when is None or re.search(r"\bi sent\b", low):
        interp.message_intent = "delivery_claim"
        interp.requires_clarification = True
        interp.summary = "Sender says the work was delivered. Not independently verified."
        interp.uncertainties.append("No file, link or recipient confirmation on record.")
    elif when is not None:
        due = task.get("due_date")
        confirm = bool(re.search(r"\b(still|can still|on track|we're good|will make)\b", low)) and due == when.isoformat()
        interp.message_intent = "commitment_confirmation" if confirm or re.search(r"\bcan still\b", low) else "timeline_update"
        interp.new_estimated_completion = when.isoformat()
        interp.date_expression = expr
        interp.requires_task_update = due != when.isoformat()
        interp.summary = f"Sender gives {'a confirmed target' if interp.message_intent == 'commitment_confirmation' else 'a new estimate'} of {fmt_day(when)}."
        if re.search(r"\b(should|probably|hopefully|think|realistically|expect|i'll|we'll|will)\b", low):
            interp.uncertainties.append("Stated as an expectation; treat as an estimate, not proof of completion.")
    elif re.search(r"\b(waiting on|waiting for|blocked|stuck)\b", low):
        interp.message_intent = "blocker_report"
        bm = re.search(r"waiting (on|for) ([\w ]+)", low)
        blocker = blocker or (f"Waiting on {bm.group(2).strip()}" if bm else "Blocked (unspecified)")
        interp.requires_clarification = True
        interp.summary = "Sender reports a blocker but gives no date."
    elif re.search(r"\b(i think|probably|should be fine|we're good|all good)\b", low):
        interp.message_intent = "ambiguous"
        interp.requires_clarification = True
        interp.summary = "Reply is reassuring but does not say whether the work is delivered or when."
        interp.uncertainties.append("No date, deliverable or status stated.")
    else:
        interp.requires_clarification = True
        interp.summary = "Could not determine what the reply means."
    interp.blocker = blocker
    interp.confidence = "high" if interp.message_intent in {"timeline_update", "commitment_confirmation"} and context.get(
        "association", {}).get("confidence") == "high" else "medium" if interp.message_intent != "ambiguous" else "low"
    interp.requires_human_approval = True
    return interp.to_dict()


# ---------------------------------------------------------------------------- main entry point
def build_context(store: Store, message: dict[str, Any], sender: dict[str, Any], association: dict[str, Any]) -> dict[str, Any]:
    task = store.get_task(association["task_id"]) if association.get("task_id") else None
    thread = []
    if message.get("thread_id"):
        thread = [{"message_id": m["message_id"], "author": m["author_slack_id"], "ts": m["ts"], "text": m["text"]}
                  for m in store.list_slack_messages() if m["thread_id"] == message["thread_id"]]
    return {
        "now": store.now().isoformat(), "sender": {k: sender[k] for k in ("member_id", "name", "first_name", "role")},
        "association": association, "task": task, "thread": thread,
        "project_people": [{"member_id": m["member_id"], "first_name": m["first_name"], "name": m["name"]}
                           for m in store.list_members(include_off_project=False)],
    }


def process_incoming_reply(store: Store, policy: dict[str, Any], message_id: str, *, event_id: str | None = None,
                           interpreter: Interpreter | None = None) -> dict[str, Any]:
    interpreter = interpreter or rules_interpreter
    key = event_id or f"msg:{message_id}"
    prior = store.processed_event(key)
    if prior:
        return {**prior["result"], "duplicate_event": True,
                "note": f"Event {key} was already processed at {prior['processed_at']}; nothing was created again."}
    message = store.get_slack_message(message_id)
    if message is None:
        email = store.get_email_message(message_id)
        if email is None:
            raise ReplyProcessingError(f"Message {message_id} not found in the mock Slack workspace or mailbox.")
        message = {**email, "text": email["body"], "ts": email["sent_at"], "thread_id": None}  # normalize email to one shape
    sender = identify_sender(store, message)
    result: dict[str, Any] = {"message_id": message_id, "event_id": key, "text": message["text"],
                              "actions": [], "duplicate_event": False}
    if sender is None or not sender.get("verified"):
        result.update({"status": "ignored_unverified_sender",
                       "note": "Sender is not a verified team member; content recorded as untrusted, no actions taken."})
        store.record_processed_event(key, message_id, result)
        return result

    association = associate_task(store, message, sender)
    context = build_context(store, message, sender, association)
    raw = interpreter(message, context)
    errors = validate_interpretation(raw, allowed_task_ids={t["task_id"] for t in store.list_tasks()})
    if errors:
        raise ReplyProcessingError(f"Interpretation failed validation: {errors}")
    interp = dict(raw)
    # Python is authoritative for dates: re-resolve the expression against the simulated clock.
    resolved, expr = resolve_date_expression(message["text"], parse_dt(message.get("ts") or message.get("sent_at")))
    if interp.get("new_estimated_completion") and resolved and interp["new_estimated_completion"] != resolved.isoformat():
        interp.setdefault("uncertainties", []).append(
            f"Interpreter said {interp['new_estimated_completion']} but '{expr}' resolves to {resolved.isoformat()}; using {resolved.isoformat()}.")
        interp["new_estimated_completion"] = resolved.isoformat()
    if association["task_id"] and interp.get("related_task_id") != association["task_id"]:
        interp.setdefault("uncertainties", []).append(
            f"Interpreter linked {interp.get('related_task_id')}, but provenance ({association['method']}) links "
            f"{association['task_id']}; using provenance.")
        interp["related_task_id"] = association["task_id"]
    if association["task_id"] is None:
        interp["related_task_id"] = None
        interp["requires_clarification"] = True
    interp["completion_confirmed"] = False  # a message can never confirm completion on its own
    result.update({"status": "processed", "sender_id": sender["member_id"], "association": association,
                   "interpretation": interp})

    task_id = interp["related_task_id"]
    task = store.get_task(task_id) if task_id else None
    fu = store.get_followup(association["followup_id"]) if association.get("followup_id") else None
    # Impact is computed BEFORE recording the new commitment, so "baseline" means "before this reply".
    pre_impact = None
    if task and interp["message_intent"] in {"timeline_update", "commitment_confirmation"} and interp.get("new_estimated_completion"):
        pre_impact = analyze_impact(store, BusinessCalendar(policy), task["task_id"],
                                    date.fromisoformat(interp["new_estimated_completion"]))
    commitment_id = record_from_interpretation(store, interp, sender["member_id"], message) if task else None
    commitment = next((c for c in store.list_commitments(task_id) if c["commitment_id"] == commitment_id), None) if commitment_id else None
    if fu:
        store.update_followup(fu["followup_id"], status="replied", reply_status="received", last_reply_at=message.get("ts"),
                              current_commitment=describe(commitment) if commitment else interp.get("summary"))
        result["actions"].append({"type": "followup_marked_replied", "followup_id": fu["followup_id"]})
    if commitment_id:
        result["actions"].append({"type": "commitment_recorded", "commitment_id": commitment_id, "description": describe(commitment)})

    if task is None:
        if interp.get("requires_clarification"):
            _clarify(store, policy, None, sender, "no_task", result, message)
        store.record_processed_event(key, message_id, result)
        return result

    intent = interp["message_intent"]
    new_date = date.fromisoformat(interp["new_estimated_completion"]) if interp.get("new_estimated_completion") else None
    cal = BusinessCalendar(policy)

    if intent in {"timeline_update", "commitment_confirmation"} and new_date:
        impact = pre_impact or analyze_impact(store, cal, task_id, new_date)
        result["impact"] = impact
        if new_date.isoformat() != task["due_date"]:
            later = new_date > date.fromisoformat(task["due_date"])
            changes: dict[str, Any] = {
                # An estimate later than the board date proposes a new due date; an earlier one only adds a note
                # (pulling a deadline in is not something a reply should trigger).
                **({"due_date": new_date.isoformat()} if later else {}),
                "add_update": (f"{sender['first_name']} reports: \"{message['text']}\" New estimated completion "
                               f"{fmt_day(new_date)} (owner estimate, not confirmed delivery)."
                               + (f" Blocker: {interp['blocker']}." if interp.get("blocker") else "")
                               + (f" Downstream: {', '.join(impact['direct_dependents'])}." if impact["direct_dependents"] else "")),
            }
            if task["status"] == "Blocked" and not interp.get("blocker"):
                changes["status"] = "In Progress"
            _propose(store, task, changes, f"{sender['name']} gave a new estimate of {fmt_day(new_date)} for {task['name']} "
                     f"(board due {fmt_day(date.fromisoformat(task['due_date']))})"
                     + ("." if later else "; ahead of the due date, so only a note is proposed."), message, key, result)
        if intent == "timeline_update":
            _notify_downstream(store, policy, task, new_date, interp.get("blocker"), impact, message, result, interp)
        _decisions_for_impact(store, task, impact, message, result)
    elif intent == "blocker_report":
        _clarify(store, policy, task, sender, intent, result, message)
        if task["status"] not in {"Blocked", "Done"}:
            _propose(store, task, {"add_update": f"{sender['first_name']} reports a blocker: \"{message['text']}\" (no new date yet)."},
                     f"{sender['name']} reported a blocker on {task['name']}.", message, key, result)
    elif intent == "completion_claim":
        _propose(store, task, {"add_update": f"{sender['first_name']} reports: \"{message['text']}\" Not marked Done: "
                               "completion has not been verified against the Done definition."},
                 f"{sender['name']} says the work is finished; recording the claim without changing status.", message, key, result)
        _clarify(store, policy, task, sender, intent, result, message)
    elif intent == "deadline_change_request":
        _decision(store, task, f"{sender['name']} asks to move {task['task_id']} ({task['name']}): \"{message['text']}\" Approve a new date?",
                  "Deadline changes are a project-lead decision.", message, result)
        _clarify(store, policy, task, sender, intent, result, message)
    elif intent == "ownership_redirect":
        named = [store.get_member(m) for m in interp.get("mentioned_people", [])]
        named = [n for n in named if n]
        other = named[0]["first_name"] if len(named) == 1 else None
        _decision(store, task, f"{sender['name']} says {other or 'someone else'} is handling {task['task_id']} ({task['name']}). "
                  "Reassign the task?", "Reassignment needs project-lead approval and confirmation from the new owner.", message, result)
        _clarify(store, policy, task, sender, intent, result, message, other=other)
    else:  # delivery_claim, ambiguous, question, unrelated
        if intent != "unrelated":
            _clarify(store, policy, task, sender, intent if intent in {"delivery_claim"} else "ambiguous", result, message)
    if interp.get("possible_prompt_injection"):
        result["security_note"] = "Message contains instruction-like text. Treated as data; no instructions were followed."
    store.record_processed_event(key, message_id, result)
    return result


# ---------------------------------------------------------------------------- helpers
def _propose(store: Store, task: dict[str, Any], changes: dict[str, Any], reason: str, message: dict[str, Any],
             event_key: str, result: dict[str, Any]) -> None:
    dedup = f"{event_key}:{task['task_id']}"
    existing = store.proposal_by_dedup(dedup)
    if existing:
        result["actions"].append({"type": "task_update_proposed", "proposal_id": existing["proposal_id"], "deduplicated": True})
        return
    payload = {"task_id": task["task_id"], "changes": changes, "base_version": task["version"]}
    ap = create_request(store, action_type="update_task", payload=payload,
                        target=f"Monday.com task {task['task_id']} ({task['name']})", reason=reason,
                        evidence=[message["message_id"]])
    pid = new_id("PROP")
    store.insert_proposal({"proposal_id": pid, "task_id": task["task_id"], "changes": changes, "reason": reason,
                           "evidence": [message["message_id"]], "source_message_id": message["message_id"],
                           "approval_id": ap["approval_id"], "status": "pending_approval", "dedup_key": dedup,
                           "created_at": store.now().isoformat(), "created_by": "pm-agent"})
    result["actions"].append({"type": "task_update_proposed", "proposal_id": pid, "approval_id": ap["approval_id"],
                              "changes": changes})


def _notify_downstream(store: Store, policy: dict[str, Any], task: dict[str, Any], new_date: date, blocker: str | None,
                       impact: dict[str, Any], message: dict[str, Any], result: dict[str, Any], interp: dict[str, Any]) -> None:
    suggested = {s["recipient_id"]: s for s in interp.get("suggested_downstream_messages") or []}
    lead = policy["escalation"]["project_lead_member_id"]
    for a in impact["affected_tasks"]:
        if not a["direct_dependent"] or a["owner_id"] in {task["owner_id"], lead}:
            continue
        dtask = store.get_task(a["task_id"])
        text = suggested.get(a["owner_id"], {}).get("message") or draft_downstream_notice(
            store, dtask, task, new_date, blocker, a["revised_projected_finish"], a["would_miss_due_date"])
        try:
            fu = create_followup_draft(
                store, policy, task_id=a["task_id"], recipient_id=a["owner_id"], channel="slack", message=text,
                purpose="downstream_notice", question_key=f"downstream_notice:{a['task_id']}:{task['task_id']}:{new_date.isoformat()}",
                reason=f"{task['task_id']} has a new estimate ({fmt_day(new_date)}); {a['task_id']} depends on it.",
                evidence=[message["message_id"]])
            result["actions"].append({"type": "downstream_notice_drafted", "followup_id": fu["followup_id"],
                                      "approval_id": fu["approval_id"], "recipient_id": a["owner_id"], "task_id": a["task_id"],
                                      "deduplicated": fu.get("deduplicated", False)})
        except FollowupRefused as exc:
            result["actions"].append({"type": "downstream_notice_refused", "task_id": a["task_id"], "reasons": exc.reasons})


def _decisions_for_impact(store: Store, task: dict[str, Any], impact: dict[str, Any], message: dict[str, Any],
                          result: dict[str, Any]) -> None:
    for a in impact["affected_tasks"]:
        if a["would_miss_due_date"] and a["direct_dependent"]:
            _decision(store, store.get_task(a["task_id"]),
                      f"{a['task_id']} ({a['name']}) now projects to {fmt_day(date.fromisoformat(a['revised_projected_finish']))}, "
                      f"after its due date {fmt_day(date.fromisoformat(a['due_date']))}. Keep the date (and find time elsewhere) or move it?",
                      f"Knock-on effect of {task['task_id']}'s new estimate.", message, result)
    for m in impact["milestones"]:
        if m["projected_to_slip"]:
            _decision(store, None, f"Milestone '{m['name']}' is projected to slip to {m['revised_projected_finish']} "
                      f"(due {m['due_date']}). Escalation: hold the date, cut scope, or move the milestone?",
                      "A milestone change requires a management decision.", message, result, key_extra=m["milestone_id"])


def _decision(store: Store, task: dict[str, Any] | None, question: str, reason: str, message: dict[str, Any],
              result: dict[str, Any], key_extra: str = "") -> None:
    did = store.upsert_decision({"task_id": task["task_id"] if task else None, "question": question, "owner_id": "TM-ALEX",
                                 "reason": reason, "evidence": [message["message_id"]],
                                 "dedup_key": f"{message['message_id']}:{task['task_id'] if task else ''}:{key_extra}"})
    result["actions"].append({"type": "decision_requested", "decision_id": did, "question": question})


def _clarify(store: Store, policy: dict[str, Any], task: dict[str, Any] | None, sender: dict[str, Any], intent: str,
             result: dict[str, Any], message: dict[str, Any], other: str | None = None) -> None:
    if task is None:
        open_tasks = [t for t in store.list_tasks() if t["owner_id"] == sender["member_id"] and t["status"] != "Done"]
        if not open_tasks:
            return
        task_for_record = open_tasks[0]
    else:
        task_for_record = task
    text = draft_clarification(store, task, sender, intent, other)
    thread_id = message.get("thread_id")
    try:
        fu = create_followup_draft(store, policy, task_id=task_for_record["task_id"], recipient_id=sender["member_id"],
                                   channel="slack", message=text, purpose="clarification",
                                   question_key=f"clarification:{message['message_id']}",
                                   reason=f"Reply was {intent.replace('_', ' ')}; asking before changing anything about "
                                          f"{short_name(task_for_record) if task else 'an unidentified task'}.",
                                   evidence=[message["message_id"]], thread_id=thread_id)
        result["actions"].append({"type": "clarification_drafted", "followup_id": fu["followup_id"],
                                  "approval_id": fu["approval_id"], "text": text})
    except FollowupRefused as exc:
        result["actions"].append({"type": "clarification_refused", "reasons": exc.reasons})
