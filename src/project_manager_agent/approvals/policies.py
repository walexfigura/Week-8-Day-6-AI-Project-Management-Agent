"""Which actions are automatic, which need a human, and which are prohibited.

This table is enforced in code. The model cannot change it, and the model has no
tool that performs an APPROVAL_REQUIRED or PROHIBITED action directly.
"""

from __future__ import annotations

AUTOMATIC = {
    "read_project", "read_task", "analyze_dependencies", "read_communications", "assess_health",
    "draft_message", "propose_task_update", "create_followup_record", "prepare_briefing",
}

APPROVAL_REQUIRED = {
    "send_slack_message", "send_email", "update_task",  # update_task covers status, due date, owner, notes
    "change_dependencies", "move_milestone", "send_announcement",
}

PROHIBITED = {
    "send_live_message", "modify_live_board", "delete_task", "change_permissions",
    "financial_commitment", "message_unapproved_recipient",
}

# What each task-change field counts as, for display in approval prompts.
CHANGE_FIELD_LABELS = {
    "status": "Change task status",
    "due_date": "Change task deadline",
    "owner_id": "Reassign task owner",
    "estimated_workdays": "Change remaining estimate",
    "add_update": "Post an update to the task",
}


class PolicyViolation(PermissionError):
    pass


def classify(action_type: str) -> str:
    if action_type in PROHIBITED:
        return "prohibited"
    if action_type in APPROVAL_REQUIRED:
        return "approval_required"
    if action_type in AUTOMATIC:
        return "automatic"
    return "prohibited"  # unknown actions are denied by default


def require_requestable(action_type: str) -> None:
    kind = classify(action_type)
    if kind == "prohibited":
        raise PolicyViolation(f"Action {action_type!r} is prohibited by policy and cannot be requested.")
