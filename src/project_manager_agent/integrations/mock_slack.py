"""Simulated Slack workspace. Sending creates a persisted outgoing record - nobody is contacted."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ..approvals.requests import ApprovalError, verify_for_execution
from ..storage import Store, new_id


class RecipientError(ApprovalError):
    pass


class MockSlack:
    mode = "mock"

    def __init__(self, store: Store, *, fail_sends: bool = False):
        self.store = store
        self.fail_sends = fail_sends  # used by tests to simulate an outage
        self.bot_id = store.get_meta("slack_bot_user_id") or "U00PMBOT"

    def _authorized_channels(self) -> set[str]:
        return {c["channel_id"] for c in self.store.list_channels() if self.bot_id in c.get("members", [])}

    def search(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        q = query.lower().strip()
        allowed = self._authorized_channels()
        hits = [m for m in self.store.list_slack_messages()
                if m["channel_id"] in allowed and (q in m["text"].lower() or q in " ".join(m["task_refs"]).lower())]
        return hits[-limit:]

    def get_thread(self, thread_id: str) -> list[dict[str, Any]]:
        allowed = self._authorized_channels()
        return [m for m in self.store.list_slack_messages() if m["thread_id"] == thread_id and m["channel_id"] in allowed]

    def dm_channel_for(self, member: dict[str, Any]) -> str:
        for ch in self.store.list_channels():
            if ch.get("type") == "im" and member["slack_user_id"] in ch.get("members", []):
                return ch["channel_id"]
        raise RecipientError(f"No DM channel with {member['member_id']}.")

    def validate_recipient(self, recipient_id: str) -> dict[str, Any]:
        member = self.store.get_member(recipient_id)
        if member is None:
            raise RecipientError(f"{recipient_id!r} is not in the team directory.")
        if not member.get("verified") or not member.get("slack_user_id"):
            raise RecipientError(f"{recipient_id} has no verified Slack identity.")
        if not member.get("on_project"):
            raise RecipientError(f"{member['name']} is not on this project; refusing to message them.")
        return member

    def send_approved_message(self, approval_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        verify_for_execution(self.store, approval_id, payload, "send_slack_message")
        member = self.validate_recipient(payload["recipient_id"])
        if payload["channel_id"] != self.dm_channel_for(member):
            raise RecipientError("Approved channel does not match the recipient's DM channel.")
        existing = self.store.slack_message_by_key(idempotency_key=approval_id)
        if existing:  # retry of an already-sent approval: no duplicate message
            return {"ok": True, "duplicate_suppressed": True, "message_id": existing["message_id"],
                    "channel_id": existing["channel_id"], "thread_id": existing["thread_id"], "ts": existing["ts"]}
        if self.fail_sends:
            raise ConnectionError("Simulated Slack outage (mock).")
        mid = new_id("MSG-OUT")
        ts = self.store.now().isoformat()
        thread_id = payload.get("thread_id") or mid
        self.store.insert_slack_message({"message_id": mid, "channel_id": payload["channel_id"], "thread_id": thread_id,
                                         "author_slack_id": self.bot_id, "ts": ts, "text": payload["text"],
                                         "task_refs": [payload["task_id"]] if payload.get("task_id") else [],
                                         "direction": "outgoing", "idempotency_key": approval_id})
        return {"ok": True, "system": "slack(mock)", "message_id": mid, "channel_id": payload["channel_id"],
                "thread_id": thread_id, "ts": ts, "note": "Recorded in the mock Slack workspace. No real message was sent."}

    def inject_incoming(self, *, member_id: str, text: str, channel_id: str, thread_id: str | None,
                        event_id: str, ts: datetime) -> tuple[dict[str, Any], bool]:
        """Simulate Slack delivering a user's message event. Returns (message, was_duplicate_delivery)."""
        existing = self.store.slack_message_by_key(event_id=event_id)
        if existing:
            return existing, True
        member = self.store.get_member(member_id)
        if member is None:
            raise RecipientError(f"Unknown member {member_id}")
        mid = new_id("MSG-IN")
        self.store.insert_slack_message({"message_id": mid, "channel_id": channel_id, "thread_id": thread_id,
                                         "author_slack_id": member["slack_user_id"], "ts": ts.isoformat(), "text": text,
                                         "task_refs": [], "direction": "incoming", "event_id": event_id})
        return self.store.get_slack_message(mid), False  # type: ignore[return-value]
