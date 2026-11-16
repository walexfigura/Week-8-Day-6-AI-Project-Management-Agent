"""Executors for approved actions + the bookkeeping that follows them.

`approvals.requests.execute` looks up the approved request and calls one of these.
Each executor calls the (mock) integration, which re-verifies the approval hash,
and then updates the agent's own records (follow-up sent time, next allowed
follow-up, resolved decisions...).
"""

from __future__ import annotations

from typing import Any

from .approvals.requests import execute
from .clock import BusinessCalendar
from .integrations.mock_gmail import MockGmail
from .integrations.mock_monday import MockMonday
from .integrations.mock_slack import MockSlack
from .storage import Store


def build_executors(store: Store, policy: dict[str, Any], *, slack: MockSlack | None = None,
                    gmail: MockGmail | None = None, monday: MockMonday | None = None,
                    enforce_working_hours: bool = True) -> dict[str, Any]:
    slack = slack or MockSlack(store)
    gmail = gmail or MockGmail(store)
    monday = monday or MockMonday(store)
    cal = BusinessCalendar(policy)
    gap = policy["followups"]["min_working_hours_between_followups"]

    def _defer_if_outside_hours() -> dict[str, Any] | None:
        now = store.now()
        if enforce_working_hours and not cal.is_working_time(now):
            return {"ok": False, "deferred": True, "scheduled_for": cal.next_working_time(now).isoformat(),
                    "reason": "Outside working hours/weekend/holiday per config/policy.json. Approval kept; "
                              "re-run apply-approved after advancing the clock."}
        return None

    def _after_send(payload: dict[str, Any], result: dict[str, Any]) -> None:
        fid = payload.get("followup_id")
        if not fid:
            return
        now = store.now()
        store.update_followup(fid, status="sent", sent_at=result.get("ts") or now.isoformat(),
                              external_message_id=result["message_id"], thread_id=result.get("thread_id"),
                              reply_status="awaiting", next_allowed_at=cal.add_working_hours(now, gap).isoformat())

    def send_slack(approval_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        deferred = _defer_if_outside_hours()
        if deferred:
            return deferred
        result = slack.send_approved_message(approval_id, payload)
        _after_send(payload, result)
        return result

    def send_email(approval_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        deferred = _defer_if_outside_hours()
        if deferred:
            return deferred
        result = gmail.send_approved_email(approval_id, payload)
        _after_send(payload, result)
        return result

    def update_task(approval_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        result = monday.apply_approved_task_update(approval_id, payload)
        for d in store.list_decisions():
            if d["task_id"] == payload["task_id"] and payload.get("resolves_decision") == d["decision_id"]:
                store.close_decision(d["decision_id"])
        if payload["changes"].get("status") == "Done":
            store.resolve_blockers(payload["task_id"])
        return result

    return {"send_slack_message": send_slack, "send_email": send_email, "update_task": update_task}


def apply_approved(store: Store, policy: dict[str, Any], approval_id: str, **kwargs: Any) -> dict[str, Any]:
    return execute(store, approval_id, build_executors(store, policy, **kwargs))
