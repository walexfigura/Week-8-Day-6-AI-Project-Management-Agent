import pytest

from project_manager_agent.communications.followups import (
    FollowupRefused,
    create_followup_draft,
    evaluate_followup,
    select_followups,
)


def test_message_mentions_known_context(env):
    fu = env.draft_sarah()
    assert fu["draft"].startswith("Hey Sarah!")
    assert "James" in fu["draft"] and "Kevin" in fu["draft"]
    assert "Lumen" in fu["draft"]  # doesn't ask what the photographer email already told us


def test_no_duplicate_draft(env):  # Test 6
    env.draft_sarah()
    r2 = select_followups(env.store, env.policy)
    assert r2[0]["recommendation"] == "already_queued"
    again = create_followup_draft(env.store, env.policy, task_id="TASK-002", recipient_id="TM-SARAH", channel="slack",
                                  message="Hey Sarah! Any news on the photos?", purpose="status_check", reason="test")
    assert again["deduplicated"] is True
    assert len(env.store.list_followups(task_id="TASK-002")) == 1


def test_frequency_limit_after_send(env):  # Test 15
    env.send_sarah()
    d = evaluate_followup(env.store, env.policy, "TASK-002", "TM-SARAH")
    assert d["action"] == "wait"
    env.store.advance_clock(2)  # two hours later, the scan runs again
    assert evaluate_followup(env.store, env.policy, "TASK-002", "TM-SARAH")["action"] == "wait"
    env.store.advance_clock(24)  # next day 11:30, past 8 working hours
    assert evaluate_followup(env.store, env.policy, "TASK-002", "TM-SARAH")["action"] == "followup"


def test_escalate_after_attempt_limit(env):
    env.send_sarah()
    env.store.advance_clock(24)
    fu2 = create_followup_draft(env.store, env.policy, task_id="TASK-002", recipient_id="TM-SARAH", channel="slack",
                                message="Hey Sarah! Following up on the product photos - any update on timing?",
                                purpose="status_check", reason="second attempt")
    env.approve_and_apply(fu2["approval_id"])
    env.store.advance_clock(24)
    d = evaluate_followup(env.store, env.policy, "TASK-002", "TM-SARAH")
    assert d["action"] == "escalate"


def test_exemption_blocks_followup(env):
    env.store.set_exemption("member", "TM-SARAH", "2026-03-12T23:59:00-04:00", "offsite")
    assert evaluate_followup(env.store, env.policy, "TASK-002", "TM-SARAH")["allowed"] is False


def test_wrong_or_ambiguous_recipient_refused(env):  # Test 20
    with pytest.raises(FollowupRefused):  # Sarah Kim is a real directory member but not on the project
        create_followup_draft(env.store, env.policy, task_id="TASK-002", recipient_id="TM-SARAH-K", channel="slack",
                              message="Hey Sarah! Checking on the product photos.", purpose="downstream_notice", reason="x")
    with pytest.raises(FollowupRefused):
        create_followup_draft(env.store, env.policy, task_id="TASK-002", recipient_id="TM-NOBODY", channel="slack",
                              message="Hey! Photos?", purpose="downstream_notice", reason="x")
    with pytest.raises(FollowupRefused):  # attacker address from the injected email is not an approved contact
        create_followup_draft(env.store, env.policy, task_id="TASK-010", recipient_id="billing@crestline-payments.example",
                              channel="email", subject="Pricing", message="Hi, pricing attached.", purpose="downstream_notice",
                              reason="x")


def test_name_lookup_is_ambiguous(env):
    from project_manager_agent.tools.registry import ToolContext, build_registry
    reg = build_registry()
    ctx = ToolContext(store=env.store, policy=env.policy, trace_id="t", settings=env.settings)
    out = reg.call(ctx, "find_team_member", {"name": "Sarah"})
    assert out["ambiguous"] is True and len(out["matches"]) == 2


def test_lint_rejects_generic_message(env):
    with pytest.raises(FollowupRefused) as exc:
        create_followup_draft(env.store, env.policy, task_id="TASK-002", recipient_id="TM-SARAH", channel="slack",
                              message="Dear valued team member, I hope this message finds you well.", purpose="status_check",
                              reason="x")
    assert any("valued" in r for r in exc.value.reasons)


def test_send_deferred_outside_working_hours(env):
    from project_manager_agent.actions import apply_approved
    from project_manager_agent.approvals.requests import decide
    fu = env.draft_sarah()
    env.store.set_now(env.store.now().replace(hour=19))
    decide(env.store, fu["approval_id"], approve=True, approver="Test Human")
    res = apply_approved(env.store, env.policy, fu["approval_id"])
    assert res["deferred"] is True
    assert not [m for m in env.store.list_slack_messages() if m["direction"] == "outgoing"]


def test_escalation_creates_lead_decision(env):
    from project_manager_agent.agent import offline_investigate
    env.send_sarah()
    env.store.advance_clock(24)
    fu2 = create_followup_draft(env.store, env.policy, task_id="TASK-002", recipient_id="TM-SARAH", channel="slack",
                                message="Hey Sarah! Following up on the product photos - any update on timing?",
                                purpose="status_check", reason="second attempt")
    env.approve_and_apply(fu2["approval_id"])
    env.store.advance_clock(24)
    offline_investigate(env.store, env.policy)
    assert any(d["dedup_key"].startswith("escalation:TASK-002") for d in env.store.list_decisions())
    assert len([f for f in env.store.list_followups(task_id="TASK-002") if f["purpose"] == "status_check"]) == 2
