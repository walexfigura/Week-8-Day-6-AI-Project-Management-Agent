"""Tool registry: schemas, permissions, argument validation and safe execution.

Permission levels:
  read     - reads mock systems / knowledge base
  analyze  - deterministic computations (dependencies, schedule, health)
  draft    - creates a follow-up draft + approval request (never sends)
  propose  - creates a task-update proposal + approval request (never applies)

There is deliberately no tool for sending, approving or applying. Those live
behind the CLI and require a human.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ..storage import Store

FORBIDDEN_TOOL_PREFIXES = ("send_", "approve", "apply", "delete", "execute", "set_permission")


@dataclass
class ToolContext:
    store: Store
    policy: dict[str, Any]
    trace_id: str
    settings: Any = None
    drafts_created: list[str] = field(default_factory=list)
    proposals_created: list[str] = field(default_factory=list)


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[[ToolContext, dict[str, Any]], dict[str, Any]]
    permission: str = "read"

    def schema(self) -> dict[str, Any]:
        return {"type": "function", "name": self.name, "description": self.description,
                "parameters": self.parameters, "strict": True}


def obj(properties: dict[str, Any]) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


S = {"type": "string"}
NS = {"type": ["string", "null"]}


class ToolRegistry:
    def __init__(self) -> None:
        self.tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if tool.name.startswith(FORBIDDEN_TOOL_PREFIXES):
            raise ValueError(f"Tool {tool.name!r} would perform a consequential action; not allowed for the model.")
        if tool.permission not in {"read", "analyze", "draft", "propose"}:
            raise ValueError(f"Bad permission {tool.permission!r}")
        self.tools[tool.name] = tool

    def schemas(self, allowed: list[str] | None = None) -> list[dict[str, Any]]:
        return [t.schema() for n, t in self.tools.items() if allowed is None or n in allowed]

    def call(self, ctx: ToolContext, name: str, raw_args: str | dict[str, Any], *, allowed: list[str] | None = None) -> dict[str, Any]:
        tool = self.tools.get(name)
        if tool is None or (allowed is not None and name not in allowed):
            return {"ok": False, "error": f"Tool {name!r} is not available for this task."}
        try:
            args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
        except json.JSONDecodeError as exc:
            return {"ok": False, "error": f"Arguments were not valid JSON: {exc}"}
        missing = [k for k in tool.parameters.get("required", []) if k not in args]
        extra = [k for k in args if k not in tool.parameters.get("properties", {})]
        if missing or extra:
            return {"ok": False, "error": f"Invalid arguments. Missing: {missing}. Unexpected: {extra}."}
        try:
            result = tool.handler(ctx, args)
        except KeyError as exc:
            return {"ok": False, "error": f"Not found: {exc}"}
        except Exception as exc:  # tools return errors to the model instead of crashing the loop
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        return {"ok": True, **result} if "ok" not in result else result


def summarize_result(result: dict[str, Any], limit: int) -> str:
    text = json.dumps(result, default=str, ensure_ascii=False)
    if len(text) > limit:
        text = text[:limit] + f'... [truncated {len(text) - limit} chars]"'
    return text


def build_registry() -> ToolRegistry:
    from . import gmail, monday, project_knowledge, slack

    reg = ToolRegistry()
    for module in (monday, slack, gmail, project_knowledge):
        for tool in module.TOOLS:
            reg.register(tool)
    return reg
