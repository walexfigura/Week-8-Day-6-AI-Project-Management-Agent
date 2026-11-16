"""Simulated Monday.com board backed by SQLite.

Reads are free. The only write path is `apply_approved_task_update`, which
re-verifies the approval (status + payload hash) and the task version first.
"""

from __future__ import annotations

from typing import Any

from ..approvals.requests import ApprovalError, verify_for_execution
from ..models import PROPOSABLE_FIELDS, TASK_STATUSES
from ..storage import Store


class MockMonday:
    mode = "mock"

    def __init__(self, store: Store):
        self.store = store

    def get_project(self, project_id: str) -> dict[str, Any] | None:
        return self.store.get_project(project_id)

    def get_tasks(self, project_id: str) -> list[dict[str, Any]]:
        return self.store.list_tasks(project_id)

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        return self.store.get_task(task_id)

    def get_updates(self, task_id: str) -> list[dict[str, Any]]:
        return self.store.list_updates(task_id)

    def apply_approved_task_update(self, approval_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        verify_for_execution(self.store, approval_id, payload, "update_task")
        task = self.store.get_task(payload["task_id"])
        if task is None:
            raise ApprovalError(f"Task {payload['task_id']} no longer exists.")
        changes = payload["changes"]
        unknown = set(changes) - PROPOSABLE_FIELDS
        if unknown:
            raise ApprovalError(f"Fields {sorted(unknown)} cannot be changed by this workflow.")
        if task["version"] != payload["base_version"]:
            raise ApprovalError(f"Task {task['task_id']} changed since this was proposed (version {payload['base_version']} -> "
                                f"{task['version']}). Re-run the analysis and request a fresh approval.")
        before = {k: task.get(k) for k in changes if k != "add_update"}
        updated = dict(task)
        if "status" in changes:
            if changes["status"] not in TASK_STATUSES:
                raise ApprovalError(f"Unknown status {changes['status']!r}.")
            updated["status"] = changes["status"]
            if changes["status"] == "Done":
                updated["completed_at"] = self.store.now().date().isoformat()
        if "due_date" in changes:
            updated["due_date"] = changes["due_date"]
        if "estimated_workdays" in changes:
            updated["estimated_workdays"] = int(changes["estimated_workdays"])
        if "owner_id" in changes:
            member = self.store.get_member(changes["owner_id"])
            if not member or not member.get("on_project"):
                raise ApprovalError(f"{changes['owner_id']} is not a verified project member.")
            updated["owner_id"] = changes["owner_id"]
        new_version = task["version"]
        if before:
            new_version = self.store.write_task(updated, expected_version=task["version"])
        update_id = None
        if changes.get("add_update"):
            update_id = self.store.add_update(task["task_id"], "U00PMBOT",
                                              f"{changes['add_update']} [posted by PM agent; approved {approval_id}]",
                                              source=f"agent:{approval_id}")
        after = {k: updated.get(k) for k in before}
        self.store.log_change(task["task_id"], approval_id, before, after)
        self.store.snapshot_task(self.store.get_task(task["task_id"]), f"approved change {approval_id}")  # type: ignore[arg-type]
        return {"ok": True, "system": "monday(mock)", "task_id": task["task_id"], "before": before, "after": after,
                "update_id": update_id, "new_version": new_version}
