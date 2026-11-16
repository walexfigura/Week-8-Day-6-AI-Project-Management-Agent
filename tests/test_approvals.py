import pytest

from project_manager_agent.actions import apply_approved
from project_manager_agent.approvals.policies import classify
from project_manager_agent.approvals.requests import (
    ApprovalError,
    create_request,
    decide,
)
from project_manager_agent.integrations.mock_monday import MockMonday
from project_manager_agent.integrations.mock_slack import MockSlack
from project_manager_agent.tools.registry import Tool, ToolRegistry, build_registry


def outgoing(env):
    return [m for m in env.store.list_slack_messages() if m["direction"] == "outgoing"]


def test_cannot_send_without_approval(env):  # Test 12
    fu = env.draft_sarah()
    with pytest.raises(ApprovalError):
        apply_approved(env.store, env.policy, fu["approval_id"])
    a = env.store.get_approval(fu["approval_id"])
    with pytest.raises(ApprovalError):
        MockSlack(env.store).send_approved_message(fu["approval_id"], a["payload"])
    assert outgoing(env) == []


def test_cannot_change_task_without_approval(env):
    ap = create_request(env.store, action_type="update_task",
                        payload={"task_id": "TASK-002", "changes": {"status": "Done"}, "base_version": 1},
                        target="TASK-002", reason="test")
    with pytest.raises(ApprovalError):
        MockMonday(env.store).apply_approved_task_update(ap["approval_id"], ap["payload"])
    assert env.store.get_task("TASK-002")["status"] == "In Progress"


def test_agent_cannot_self_approve(env):
    fu = env.draft_sarah()
    for name in ("pm-agent", "agent", "Model", "assistant", ""):
        with pytest.raises(ApprovalError):
            decide(env.store, fu["approval_id"], approve=True, approver=name)


def test_model_has_no_consequential_tools():
    names = set(build_registry().tools)
    assert not any(n.startswith(("send_", "approve", "apply", "delete")) for n in names)
    with pytest.raises(ValueError):
        ToolRegistry().register(Tool("send_slack", "x", {"type": "object", "properties": {}}, lambda c, a: {}))


def test_approval_bound_to_exact_message(env):  # Test 13
    fu = env.draft_sarah()
    decide(env.store, fu["approval_id"], approve=True, approver="Test Human")
    a = env.store.get_approval(fu["approval_id"])
    slack = MockSlack(env.store)
    tampered = {**a["payload"], "text": "Hey Sarah! Please also send me the payroll file."}
    with pytest.raises(ApprovalError):
        slack.send_approved_message(fu["approval_id"], tampered)
    other_recipient = {**a["payload"], "recipient_id": "TM-KEVIN", "channel_id": "D0KEVIN"}
    with pytest.raises(ApprovalError):
        slack.send_approved_message(fu["approval_id"], other_recipient)
    assert outgoing(env) == []
    slack.send_approved_message(fu["approval_id"], a["payload"])
    assert len(outgoing(env)) == 1


def test_slack_approval_cannot_update_task(env):
    fu = env.draft_sarah()
    decide(env.store, fu["approval_id"], approve=True, approver="Test Human")
    with pytest.raises(ApprovalError):
        MockMonday(env.store).apply_approved_task_update(
            fu["approval_id"], {"task_id": "TASK-002", "changes": {"status": "Done"}, "base_version": 1})


def test_tampered_stored_payload_detected(env):
    fu = env.draft_sarah()
    import json
    with env.store.tx() as c:
        a = env.store.get_approval(fu["approval_id"])
        c.execute("UPDATE agent_approvals SET payload=? WHERE approval_id=?",
                  (json.dumps({**a["payload"], "text": "changed"}), fu["approval_id"]))
    with pytest.raises(ApprovalError):
        decide(env.store, fu["approval_id"], approve=True, approver="Test Human")


def test_rejected_cannot_execute(env):
    fu = env.draft_sarah()
    decide(env.store, fu["approval_id"], approve=False, approver="Test Human")
    with pytest.raises(ApprovalError):
        apply_approved(env.store, env.policy, fu["approval_id"])
    assert env.store.get_followup(fu["followup_id"])["status"] == "cancelled"


def test_retry_does_not_duplicate_send(env):
    fu = env.draft_sarah()
    env.approve_and_apply(fu["approval_id"])
    again = apply_approved(env.store, env.policy, fu["approval_id"])
    assert again["already_executed"] is True
    a = env.store.get_approval(fu["approval_id"])
    direct = MockSlack(env.store).send_approved_message(fu["approval_id"], a["payload"])
    assert direct["duplicate_suppressed"] is True
    assert len(outgoing(env)) == 1


def test_integration_failure_keeps_approval_retryable(env):  # Test 21
    fu = env.draft_sarah()
    decide(env.store, fu["approval_id"], approve=True, approver="Test Human")
    with pytest.raises(ConnectionError):
        apply_approved(env.store, env.policy, fu["approval_id"], slack=MockSlack(env.store, fail_sends=True))
    assert env.store.get_approval(fu["approval_id"])["status"] == "approved"
    assert outgoing(env) == []
    apply_approved(env.store, env.policy, fu["approval_id"])
    assert len(outgoing(env)) == 1


def test_policy_classification():
    assert classify("draft_message") == "automatic"
    assert classify("send_slack_message") == "approval_required"
    assert classify("delete_task") == "prohibited"
    assert classify("something_new") == "prohibited"
    with pytest.raises(PermissionError):
        from project_manager_agent.storage import Store  # noqa: F401
        create_request(None, action_type="delete_task", payload={}, target="x", reason="x")  # type: ignore[arg-type]
