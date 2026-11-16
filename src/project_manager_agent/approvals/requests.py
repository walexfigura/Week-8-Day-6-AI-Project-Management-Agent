"""Approval requests: create, decide, execute - each bound to an exact payload hash.

Rules enforced here:

1. Every consequential action is stored as an approval request holding the *exact*
   payload (message text + recipient, or task changes + base version) and its SHA-256.
2. Only a human approver name passes validation. The requester (the agent) cannot approve,
   and reserved names such as "agent", "model" or "assistant" are rejected.
3. Execution re-reads the request from the database, re-hashes the stored payload, and the
   integration re-checks the hash of the payload it was given. An approval for one message
   can never send a different message or reach a different recipient.
4. Execution is idempotent: running it twice returns the first result.
"""

from __future__ import annotations

from typing import Any

from ..config import RESERVED_APPROVER_NAMES
from ..models import canonical_hash
from ..storage import Store, new_id
from .policies import classify, require_requestable


class ApprovalError(PermissionError):
    pass


def create_request(store: Store, *, action_type: str, payload: dict[str, Any], target: str, reason: str,
                   evidence: list[str] | None = None, requested_by: str = "pm-agent") -> dict[str, Any]:
    require_requestable(action_type)
    if classify(action_type) != "approval_required":
        raise ApprovalError(f"{action_type!r} does not go through the approval queue.")
    ap = {"approval_id": new_id("APR"), "action_type": action_type, "payload": payload,
          "payload_hash": canonical_hash(payload), "target": target, "reason": reason,
          "evidence": evidence or [], "requested_at": store.get_meta("sim_now"), "requested_by": requested_by}
    store.insert_approval(ap)
    return store.get_approval(ap["approval_id"])  # type: ignore[return-value]


def validate_approver(approver: str, request: dict[str, Any]) -> str:
    name = (approver or "").strip()
    if not name:
        raise ApprovalError("An approver name is required.")
    if name.lower() in RESERVED_APPROVER_NAMES or name == request["requested_by"]:
        raise ApprovalError(f"{name!r} cannot approve: the agent may not approve its own proposed actions.")
    return name


def decide(store: Store, approval_id: str, *, approve: bool, approver: str, note: str = "",
           expected_hash: str | None = None) -> dict[str, Any]:
    req = store.get_approval(approval_id)
    if req is None:
        raise ApprovalError(f"No approval request {approval_id!r}.")
    if req["status"] != "pending":
        raise ApprovalError(f"{approval_id} is already {req['status']}; decisions are final.")
    name = validate_approver(approver, req)
    if expected_hash and expected_hash != req["payload_hash"]:
        raise ApprovalError("The action shown to you no longer matches the stored request. Re-run `approvals`.")
    if canonical_hash(req["payload"]) != req["payload_hash"]:
        raise ApprovalError("Stored payload does not match its hash (tampered). Request rejected.")
    store.update_approval(approval_id, status="approved" if approve else "rejected", decided_by=name,
                          decided_at=store.get_meta("sim_now"), decision_note=note)
    _sync_linked(store, store.get_approval(approval_id))  # type: ignore[arg-type]
    return store.get_approval(approval_id)  # type: ignore[return-value]


def _sync_linked(store: Store, req: dict[str, Any]) -> None:
    """Keep follow-up / proposal records in step with their approval."""
    for fu in store.list_followups():
        if fu["approval_id"] == req["approval_id"]:
            mapping = {"approved": "approved", "rejected": "cancelled", "executed": "sent", "failed": "approved"}
            if req["status"] in mapping and fu["status"] not in {"replied", "closed"}:
                store.update_followup(fu["followup_id"], status=mapping[req["status"]])
    pr = store.proposal_by_approval(req["approval_id"])
    if pr:
        mapping = {"approved": "approved", "rejected": "rejected", "executed": "applied", "failed": "failed"}
        if req["status"] in mapping:
            store.update_proposal(pr["proposal_id"], status=mapping[req["status"]])


def verify_for_execution(store: Store, approval_id: str, payload: dict[str, Any], action_type: str) -> dict[str, Any]:
    """Called by integrations immediately before a consequential write."""
    req = store.get_approval(approval_id)
    if req is None:
        raise ApprovalError(f"No approval request {approval_id!r}.")
    if req["action_type"] != action_type:
        raise ApprovalError(f"{approval_id} approves {req['action_type']}, not {action_type}.")
    if req["status"] not in {"approved", "executed"}:
        raise ApprovalError(f"{approval_id} is {req['status']}; a human must approve it first.")
    if canonical_hash(payload) != req["payload_hash"] or canonical_hash(req["payload"]) != req["payload_hash"]:
        raise ApprovalError(f"{approval_id} was approved for a different payload. A new approval is required.")
    return req


def execute(store: Store, approval_id: str, executors: dict[str, Any]) -> dict[str, Any]:
    req = store.get_approval(approval_id)
    if req is None:
        raise ApprovalError(f"No approval request {approval_id!r}.")
    if req["status"] == "executed":
        return {**(req["execution_result"] or {}), "already_executed": True}
    if req["status"] != "approved":
        raise ApprovalError(f"{approval_id} is {req['status']}. Only approved requests can be applied.")
    handler = executors.get(req["action_type"])
    if handler is None:
        raise ApprovalError(f"No executor for {req['action_type']}.")
    try:
        result = handler(approval_id, req["payload"])
    except ApprovalError:
        raise
    except Exception as exc:  # integration failure: record it, keep approval re-tryable
        store.update_approval(approval_id, execution_result={"ok": False, "error": str(exc)})
        raise
    if result.get("deferred"):
        store.update_approval(approval_id, execution_result=result)
        return result
    store.update_approval(approval_id, status="executed", executed_at=store.get_meta("sim_now"), execution_result=result)
    _sync_linked(store, store.get_approval(approval_id))  # type: ignore[arg-type]
    return result
