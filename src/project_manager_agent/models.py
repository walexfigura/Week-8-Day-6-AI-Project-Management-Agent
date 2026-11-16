"""Shared vocabulary: statuses, intents, and the structured reply interpretation.

Records themselves are plain dicts loaded from SQLite (easy to print, serialize
and hand to the model as JSON). The constants below are the contract.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any

TASK_STATUSES = ["Not Started", "In Progress", "Blocked", "Waiting", "At Risk", "Done"]
PRIORITIES = {"Critical": 4, "High": 2, "Medium": 1, "Low": 0}

# Fields the agent may *propose* changing on an official task. Anything else is rejected.
PROPOSABLE_FIELDS = {"status", "due_date", "owner_id", "add_update", "estimated_workdays"}

REPLY_INTENTS = [
    "timeline_update",          # owner gives a new (estimated) completion date
    "commitment_confirmation",  # owner confirms they can still hit a date
    "blocker_report",           # owner reports a blocker without a date
    "completion_claim",         # "done", "finished" - must be verified before status=Done
    "delivery_claim",           # "I sent it" - unverified until the recipient/file confirms
    "deadline_change_request",  # "can we push it?" - a management decision
    "ownership_redirect",       # "ask Kevin" - possible reassignment, needs verification
    "question",                 # the person asked something back
    "ambiguous",                # cannot tell; ask a clarifying question
    "unrelated",
]

FOLLOWUP_OPEN_STATES = {"drafted", "pending_approval", "approved", "sent"}
APPROVAL_STATES = ["pending", "approved", "rejected", "executed", "failed", "superseded"]


def canonical_hash(payload: dict[str, Any]) -> str:
    """SHA-256 of canonical JSON. Approvals are bound to this hash, so an approval for one
    message or change can never authorize a different one."""
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


@dataclass
class ReplyInterpretation:
    related_task_id: str | None
    message_intent: str
    new_estimated_completion: str | None = None
    date_expression: str | None = None
    blocker: str | None = None
    completion_confirmed: bool = False
    requires_task_update: bool = False
    requires_human_approval: bool = True
    requires_clarification: bool = False
    clarification_question: str | None = None
    mentioned_people: list[str] = field(default_factory=list)
    confidence: str = "medium"          # low | medium | high
    summary: str = ""
    uncertainties: list[str] = field(default_factory=list)
    possible_prompt_injection: bool = False
    interpreter: str = "rules"           # rules | model

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def validate_interpretation(data: dict[str, Any], *, allowed_task_ids: set[str]) -> list[str]:
    """Python-side validation of an interpretation, whoever produced it."""
    errors: list[str] = []
    if data.get("message_intent") not in REPLY_INTENTS:
        errors.append(f"message_intent must be one of {REPLY_INTENTS}")
    tid = data.get("related_task_id")
    if tid is not None and tid not in allowed_task_ids:
        errors.append(f"related_task_id {tid!r} is not a known task")
    if data.get("confidence") not in {"low", "medium", "high"}:
        errors.append("confidence must be low, medium or high")
    est = data.get("new_estimated_completion")
    if est:
        try:
            from datetime import date
            date.fromisoformat(est)
        except ValueError:
            errors.append("new_estimated_completion must be YYYY-MM-DD or null")
    if data.get("completion_confirmed") and data.get("message_intent") != "completion_claim":
        errors.append("completion_confirmed can only be true for a completion_claim")
    return errors
