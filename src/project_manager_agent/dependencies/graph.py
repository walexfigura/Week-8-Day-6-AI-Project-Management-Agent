"""Task dependency graph built from task IDs.

An edge (task, depends_on) means `task` cannot finish until `depends_on` is Done.
"Upstream" = what a task waits for. "Downstream" = what waits for the task.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from typing import Any


class CycleError(ValueError):
    def __init__(self, cycle: list[str]):
        self.cycle = cycle
        super().__init__("Circular dependency: " + " -> ".join(cycle))


class InvalidReferenceError(ValueError):
    pass


class DependencyGraph:
    def __init__(self, task_ids: Iterable[str], edges: Iterable[tuple[str, str]]):
        self.task_ids = set(task_ids)
        self.edges = sorted(set(edges))
        self.upstream: dict[str, set[str]] = defaultdict(set)
        self.downstream: dict[str, set[str]] = defaultdict(set)
        for task, dep in self.edges:
            self.upstream[task].add(dep)
            self.downstream[dep].add(task)

    @classmethod
    def from_store(cls, store: Any) -> DependencyGraph:
        return cls([t["task_id"] for t in store.list_tasks()], store.list_dependencies())

    # ----------------------------------------------------------- validation
    def invalid_references(self) -> list[dict[str, str]]:
        problems = []
        for task, dep in self.edges:
            for role, tid in (("task_id", task), ("depends_on", dep)):
                if tid not in self.task_ids:
                    problems.append({"edge": f"{task} -> {dep}", "unknown": tid, "field": role})
            if task == dep:
                problems.append({"edge": f"{task} -> {dep}", "unknown": "", "field": "self-dependency"})
        return problems

    def find_cycle(self) -> list[str] | None:
        """Return one cycle as a path (first node repeated at the end), or None."""
        WHITE, GREY, BLACK = 0, 1, 2
        color = {t: WHITE for t in self.task_ids | set(self.upstream)}
        stack: list[str] = []

        def visit(node: str) -> list[str] | None:
            color[node] = GREY
            stack.append(node)
            for nxt in sorted(self.upstream.get(node, ())):
                if color.get(nxt, WHITE) == GREY:
                    return stack[stack.index(nxt):] + [nxt]
                if color.get(nxt, WHITE) == WHITE:
                    found = visit(nxt)
                    if found:
                        return found
            stack.pop()
            color[node] = BLACK
            return None

        for node in sorted(color):
            if color[node] == WHITE:
                found = visit(node)
                if found:
                    return found
        return None

    def validate(self) -> None:
        problems = self.invalid_references()
        if problems:
            raise InvalidReferenceError(f"Invalid dependency references: {problems}")
        cycle = self.find_cycle()
        if cycle:
            raise CycleError(cycle)

    # ----------------------------------------------------------- traversal
    def _require(self, task_id: str) -> None:
        if task_id not in self.task_ids:
            raise KeyError(f"Unknown task {task_id!r}")

    def direct_dependencies(self, task_id: str) -> list[str]:
        self._require(task_id)
        return sorted(self.upstream.get(task_id, ()))

    def direct_dependents(self, task_id: str) -> list[str]:
        self._require(task_id)
        return sorted(self.downstream.get(task_id, ()))

    def _walk(self, start: str, adjacency: dict[str, set[str]]) -> list[str]:
        seen: set[str] = set()
        frontier = list(adjacency.get(start, ()))
        while frontier:
            node = frontier.pop()
            if node in seen or node == start:
                continue
            seen.add(node)
            frontier.extend(adjacency.get(node, ()))
        return sorted(seen)

    def transitive_dependencies(self, task_id: str) -> list[str]:
        self._require(task_id)
        return self._walk(task_id, self.upstream)

    def transitive_dependents(self, task_id: str) -> list[str]:
        """Everything downstream of task_id (would be held up if it slips)."""
        self._require(task_id)
        return self._walk(task_id, self.downstream)

    def topological_order(self) -> list[str]:
        self.validate()
        indegree = {t: len(self.upstream.get(t, ())) for t in self.task_ids}
        ready = sorted(t for t, n in indegree.items() if n == 0)
        order: list[str] = []
        while ready:
            node = ready.pop(0)
            order.append(node)
            for nxt in sorted(self.downstream.get(node, ())):
                indegree[nxt] -= 1
                if indegree[nxt] == 0:
                    ready.append(nxt)
            ready.sort()
        return order

    def path_exists(self, from_task: str, to_task: str) -> bool:
        return to_task in self.transitive_dependents(from_task)

    def mermaid(self, tasks: dict[str, dict[str, Any]], highlight: set[str] | None = None) -> str:
        highlight = highlight or set()
        lines = ["graph LR"]
        for tid in sorted(self.task_ids):
            t = tasks.get(tid, {})
            label = f"{tid}<br/>{t.get('name', '')}<br/>{t.get('status', '')}".replace('"', "'")
            lines.append(f'    {tid.replace("-", "")}["{label}"]')
        for task, dep in self.edges:
            lines.append(f"    {dep.replace('-', '')} --> {task.replace('-', '')}")
        for tid in sorted(highlight):
            lines.append(f"    style {tid.replace('-', '')} stroke:#d33,stroke-width:3px")
        return "\n".join(lines)
