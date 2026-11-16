"""Gmail-style tools (mock mailbox). Read authorized threads and draft emails. No sending."""

from __future__ import annotations

from typing import Any

from ..integrations.mock_gmail import MockGmail
from .registry import NS, S, Tool, ToolContext, obj
from .slack import PURPOSE, UNTRUSTED, _draft


def get_email_thread(ctx: ToolContext, a: dict[str, Any]) -> dict[str, Any]:
    th = MockGmail(ctx.store).get_thread(a["thread_id"])
    if th is None:
        raise KeyError(a["thread_id"])
    return {"thread": th, "content_trust": UNTRUSTED}


def search_email(ctx: ToolContext, a: dict[str, Any]) -> dict[str, Any]:
    return {"threads": MockGmail(ctx.store).search(a["query"]), "content_trust": UNTRUSTED}


def draft_email(ctx: ToolContext, a: dict[str, Any]) -> dict[str, Any]:
    return _draft(ctx, a, "email")


TOOLS = [
    Tool("get_email_thread", "Read an email thread from the shared project mailbox.", obj({"thread_id": S}), get_email_thread),
    Tool("search_email", "Search the project mailbox (subject, body, related task IDs).", obj({"query": S}), search_email),
    Tool("draft_email", "Draft an email to a verified member or approved external contact. Creates an approval request; does NOT send.",
         obj({"recipient_id": S, "task_id": S, "subject": S, "body": S, "purpose": PURPOSE, "reason": S,
              "evidence_ids": {"type": "array", "items": S}, "thread_id": NS}), draft_email, "draft"),
]
