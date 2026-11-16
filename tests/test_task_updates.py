import pytest

from project_manager_agent.actions import apply_approved
from project_manager_agent.approvals.requests import ApprovalError, decide
from project_manager_agent.tools.registry import ToolContext, build_registry


def _flow_to_proposal(env):
    env.send_sarah()
    res = env.reply("photography_delayed")[0]
    return next(a for a in res["actions"] if a["type"] == "task_update_proposed")


def test_proposal_requires_approval_then_persists(env):
    prop = _flow_to_proposal(env)
    assert env.store.get_task("TASK-002")["due_date"] == "2026-03-09"
    result = env.approve_and_apply(prop["approval_id"])
    t = env.store.get_task("TASK-002")
    assert t["due_date"] == "2026-03-11" and t["status"] == "In Progress" and t["version"] == 2
    assert result["before"] == {"due_date": "2026-03-09"} and result["after"] == {"due_date": "2026-03-11"}
    note = env.store.list_updates("TASK-002")[-1]
    assert "estimate" in note["body"] and prop["approval_id"] in note["body"]
    assert env.store.proposal_by_approval(prop["approval_id"])["status"] == "applied"


def test_stale_proposal_rejected(env):
    prop = _flow_to_proposal(env)
    t = env.store.get_task("TASK-002")
    env.store.write_task({**t, "status": "At Risk"}, expected_version=t["version"])  # someone edits the board meanwhile
    decide(env.store, prop["approval_id"], approve=True, approver="Test Human")
    with pytest.raises(ApprovalError, match="changed since"):
        apply_approved(env.store, env.policy, prop["approval_id"])


def test_model_tool_cannot_propose_unknown_fields_or_unverified_done(env):
    reg = build_registry()
    ctx = ToolContext(store=env.store, policy=env.policy, trace_id="t", settings=env.settings)
    base = {"status": None, "due_date": None, "owner_id": None, "add_update": None, "estimated_workdays": None}
    out = reg.call(ctx, "propose_task_update", {"task_id": "TASK-002", "changes": {**base, "status": "Done"},
                                                 "reason": "Sarah says almost done", "evidence_message_ids": []})
    assert out["ok"] is False
    out = reg.call(ctx, "propose_task_update", {"task_id": "TASK-002", "changes": {**base, "owner_id": "TM-SARAH-K"},
                                                 "reason": "x", "evidence_message_ids": []})
    assert out["ok"] is False
    out = reg.call(ctx, "propose_task_update", {"task_id": "TASK-002", "changes": {**base, "due_date": "2026-03-11"},
                                                 "reason": "owner estimate", "evidence_message_ids": ["M-1"]})
    assert out["ok"] is True and env.store.get_task("TASK-002")["due_date"] == "2026-03-09"


def test_downstream_notices_need_approval(env):
    _flow_to_proposal(env)
    notices = [f for f in env.store.list_followups() if f["purpose"] == "downstream_notice"]
    assert {f["recipient_id"] for f in notices} == {"TM-JAMES", "TM-KEVIN"}
    assert all(f["status"] == "pending_approval" for f in notices)
    assert "estimate" in notices[0]["draft"]


def test_decisions_raised_for_knock_on_slips(env):
    _flow_to_proposal(env)
    qs = " ".join(d["question"] for d in env.store.list_decisions())
    assert "TASK-007" in qs and "TASK-003" in qs
    assert env.store.get_task("TASK-007")["due_date"] == "2026-03-13"  # not changed by the agent
