from project_manager_agent.agent import offline_investigate
from project_manager_agent.communications.followups import select_followups


def test_shared_blocker_ranked_first(env):  # Test 4
    plan = select_followups(env.store, env.policy)
    assert plan[0]["task_id"] == "TASK-002"
    assert plan[0]["recommendation"] == "follow_up"
    assert "downstream_tasks" in plan[0]["components"]


def test_downstream_owners_not_contacted(env):  # Test 4
    plan = {p["task_id"]: p for p in select_followups(env.store, env.policy)}
    for tid in ("TASK-004", "TASK-007"):
        assert plan[tid]["recommendation"] == "no_contact"
        assert plan[tid]["covered_by_upstream"] == "TASK-002"


def test_investigation_drafts_only_sarah(env):
    r = offline_investigate(env.store, env.policy)
    assert len(r["followups_drafted"]) == 1
    fu = env.store.get_followup(r["followups_drafted"][0])
    assert (fu["task_id"], fu["recipient_id"], fu["channel"]) == ("TASK-002", "TM-SARAH", "slack")
    assert all(f["recipient_id"] == "TM-SARAH" for f in env.store.list_followups())


def test_existing_update_avoids_question(env):  # Test 5
    plan = {p["task_id"]: p for p in select_followups(env.store, env.policy)}
    assert plan["TASK-010"]["recommendation"] == "no_contact"
    assert plan["TASK-010"]["fresh_update"]["id"] == "UPD-1007"


def test_not_ranked_by_days_overdue_alone(env):
    plan = select_followups(env.store, env.policy)
    p = plan[0]
    assert p["components"]["overdue"] < sum(v for k, v in p["components"].items() if k != "overdue")


def test_investigate_specific_downstream_task_redirects_upstream(env):
    r = offline_investigate(env.store, env.policy, "TASK-004")
    fu = env.store.get_followup(r["followups_drafted"][0])
    assert fu["recipient_id"] == "TM-SARAH"
