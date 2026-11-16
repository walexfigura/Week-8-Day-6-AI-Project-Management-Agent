"""Simulated Gmail mailbox (projects@northstar.example). Sending records a mock outgoing email only."""

from __future__ import annotations

from typing import Any

from ..approvals.requests import verify_for_execution
from ..storage import Store, new_id
from .mock_slack import RecipientError


class MockGmail:
    mode = "mock"

    def __init__(self, store: Store, *, fail_sends: bool = False):
        self.store = store
        self.fail_sends = fail_sends
        self.mailbox = store.get_meta("gmail_mailbox") or "projects@northstar.example"

    def get_thread(self, thread_id: str) -> dict[str, Any] | None:
        meta = self.store.get_email_thread_meta(thread_id)
        if meta is None:
            return None
        return {**meta, "messages": self.store.list_email_messages(thread_id)}

    def search(self, query: str) -> list[dict[str, Any]]:
        q = query.lower()
        out = []
        for meta in self.store.list_email_threads():
            msgs = self.store.list_email_messages(meta["thread_id"])
            blob = " ".join([meta.get("subject", "")] + [m["body"] for m in msgs] + meta.get("related_task_ids", [])).lower()
            if q in blob:
                out.append({**meta, "message_count": len(msgs), "last_message_at": msgs[-1]["sent_at"] if msgs else None})
        return out

    def resolve_address(self, recipient_id: str) -> tuple[str, str]:
        member = self.store.get_member(recipient_id)
        if member:
            if not member.get("on_project") or not member.get("verified"):
                raise RecipientError(f"{member['name']} is not a verified project member.")
            return member["email"], member["name"]
        contact = self.store.get_contact(recipient_id)
        if contact and contact.get("approved_contact"):
            return contact["email"], contact["name"]
        raise RecipientError(f"{recipient_id!r} is not an approved recipient.")

    def send_approved_email(self, approval_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        verify_for_execution(self.store, approval_id, payload, "send_email")
        address, _ = self.resolve_address(payload["recipient_id"])
        if address != payload["to"]:
            raise RecipientError("Approved address does not match the directory record.")
        existing = self.store.email_by_key(approval_id)
        if existing:
            return {"ok": True, "duplicate_suppressed": True, "message_id": existing["message_id"],
                    "thread_id": existing["thread_id"]}
        if self.fail_sends:
            raise ConnectionError("Simulated Gmail outage (mock).")
        thread_id = payload.get("thread_id") or new_id("GT")
        if self.store.get_email_thread_meta(thread_id) is None:
            self.store.upsert_email_thread({"thread_id": thread_id, "subject": payload["subject"],
                                            "related_task_ids": [payload["task_id"]] if payload.get("task_id") else [],
                                            "category": "internal"})
        mid = new_id("GM-OUT")
        self.store.insert_email_message({"message_id": mid, "thread_id": thread_id, "from_addr": self.mailbox,
                                         "to_addrs": [address], "subject": payload["subject"],
                                         "sent_at": self.store.now().isoformat(), "body": payload["body"],
                                         "direction": "outgoing", "idempotency_key": approval_id})
        return {"ok": True, "system": "gmail(mock)", "message_id": mid, "thread_id": thread_id,
                "note": "Recorded in the mock mailbox. No real email was sent."}
