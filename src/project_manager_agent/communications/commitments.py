"""Commitments and blockers extracted from replies, kept with provenance."""

from __future__ import annotations

from datetime import date
from typing import Any

from ..clock import fmt_day

KIND_BY_INTENT = {
    "timeline_update": "estimate",
    "commitment_confirmation": "confirmed_date",
    "completion_claim": "completion_claim",
    "delivery_claim": "delivery_claim",
    "deadline_change_request": "change_request",
    "blocker_report": "blocker",
    "ownership_redirect": "ownership_claim",
}


def record_from_interpretation(store: Any, interp: dict[str, Any], member_id: str, message: dict[str, Any]) -> str | None:
    task_id = interp.get("related_task_id")
    kind = KIND_BY_INTENT.get(interp["message_intent"])
    if not task_id or not kind:
        return None
    if interp.get("blocker"):
        store.insert_blocker(task_id, interp["blocker"], message["message_id"])
    return store.insert_commitment({
        "task_id": task_id, "member_id": member_id, "kind": kind,
        "commitment_date": interp.get("new_estimated_completion"),
        "text": message["text"], "source_message_id": message["message_id"], "confidence": interp.get("confidence", "medium"),
    })


def describe(commitment: dict[str, Any] | None) -> str:
    if not commitment:
        return "none"
    when = f" {fmt_day(date.fromisoformat(commitment['commitment_date']))}" if commitment.get("commitment_date") else ""
    label = {"estimate": "Owner estimate", "confirmed_date": "Owner confirmed", "completion_claim": "Owner reports finished (unverified)",
             "delivery_claim": "Owner says delivered (unverified)", "change_request": "Owner requests a new date",
             "blocker": "Blocker reported", "ownership_claim": "Owner says someone else is handling it"}.get(commitment["kind"], commitment["kind"])
    return f"{label}{when} (from {commitment['source_message_id']})"
