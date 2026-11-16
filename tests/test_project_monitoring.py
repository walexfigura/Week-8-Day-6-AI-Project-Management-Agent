from project_manager_agent.monitoring.overdue_tasks import (
    date_conflicts,
    overdue_tasks,
    stale_tasks,
)
from project_manager_agent.monitoring.project_health import assess_health


def test_overdue_photography_detected(env):  # Test 1
    overdue = overdue_tasks(env.store.list_tasks(), env.store.now().date())
    assert [t["task_id"] for t in overdue] == ["TASK-002"]
    assert overdue[0]["days_overdue"] == 1


def test_done_task_never_overdue(env):
    assert all(t["task_id"] != "TASK-001" for t in overdue_tasks(env.store.list_tasks(), env.store.now().date()))


def test_health_is_at_risk_not_blocked_initially(env):
    h = assess_health(env.store, env.policy)
    assert h["classification"] == "At Risk"
    assert not any(m["projected_to_slip"] for m in h["milestones"])
    assert h["bottlenecks"][0]["task_id"] == "TASK-002"


def test_stale_social_task_detected(env):
    stale = {s["task_id"] for s in stale_tasks(env.store, env.store.now(), env.policy["followups"]["stale_update_days"])}
    assert "TASK-009" in stale
    assert "TASK-010" not in stale  # fresh update UPD-1007


def test_conflicting_slack_vs_board_flagged(make_env):  # Test 17
    e = make_env("conflicting-deadlines")
    conflicts = date_conflicts(e.store)
    assert any(c["task_id"] == "TASK-004" and c["reported_date"] == "2026-03-18" and c["board_due_date"] == "2026-03-16"
               for c in conflicts)
    h = assess_health(e.store, e.policy)
    assert any("TASK-004" in r for r in h["reasons"]["Needs Review"])
    assert e.store.get_task("TASK-004")["due_date"] == "2026-03-16"  # not silently changed


def test_conflict_reply_produces_proposal_not_change(make_env):
    e = make_env("conflicting-deadlines")
    res = e.reply("landing_page_conflict")[0]
    assert res["interpretation"]["new_estimated_completion"] == "2026-03-18"
    assert any(a["type"] == "task_update_proposed" for a in res["actions"])
    assert e.store.get_task("TASK-004")["due_date"] == "2026-03-16"
