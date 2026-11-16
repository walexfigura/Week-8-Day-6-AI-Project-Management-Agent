"""Read-only access to the knowledge base (allowlisted file names only - no arbitrary file access)."""

from __future__ import annotations

from typing import Any

from .registry import Tool, ToolContext, obj

DOCS = ["company-context", "project-management-rules", "communication-guidelines", "followup-rules",
        "approval-policies", "task-status-definitions", "project-escalation-rules"]


def get_knowledge(ctx: ToolContext, a: dict[str, Any]) -> dict[str, Any]:
    name = a["document"]
    if name not in DOCS:
        return {"ok": False, "error": f"Unknown document. Choose one of {DOCS}."}
    root = ctx.settings.knowledge_dir if ctx.settings else None
    if root is None:
        return {"ok": False, "error": "Knowledge base path unavailable."}
    return {"document": name, "content": (root / f"{name}.md").read_text(encoding="utf-8")}


TOOLS = [Tool("get_knowledge", "Read a Northstar knowledge-base document by name.",
              obj({"document": {"type": "string", "enum": DOCS}}), get_knowledge)]
