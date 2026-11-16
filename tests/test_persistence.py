import argparse

from project_manager_agent.cli import cmd_init, init_state
from project_manager_agent.monitoring.project_health import assess_health
from project_manager_agent.storage import open_store


def test_state_survives_restart(env):  # Test 14
    fu = env.send_sarah()
    env.store.close()
    reopened = open_store(env.settings)               # "close the app and reopen it"
    f2 = reopened.get_followup(fu["followup_id"])
    assert f2["status"] == "sent" and f2["reply_status"] == "awaiting"
    assert reopened.now().isoformat() == "2026-03-10T09:30:00-04:00"  # simulated clock persisted too
    env.store = reopened
    res = env.reply("photography_delayed")[0]
    prop = next(a for a in res["actions"] if a["type"] == "task_update_proposed")
    reopened.close()
    env.store = open_store(env.settings)
    env.approve_and_apply(prop["approval_id"])
    env.store.close()
    final = open_store(env.settings)
    assert final.get_task("TASK-002")["due_date"] == "2026-03-11"
    h = assess_health(final, env.policy)
    assert h["estimates_used"] == {"TASK-002": "2026-03-11"}


def test_init_does_not_reset_existing_state(env, capsys):
    env.send_sarah()
    cmd_init(env.settings, argparse.Namespace(scenario=None, force=False))
    assert "Keeping it" in capsys.readouterr().out
    assert open_store(env.settings).list_followups()


def test_reset_restores_original(env):
    env.send_sarah()
    init_state(env.settings, "launch-delay")
    s = open_store(env.settings)
    assert s.list_followups() == [] and s.list_approvals() == []
    assert s.get_task("TASK-002")["due_date"] == "2026-03-09"
    assert s.now().isoformat() == "2026-03-10T09:30:00-04:00"


def test_health_history_recorded(env):
    assess_health(env.store, env.policy, record=True)
    env.store.advance_clock(1)
    assess_health(env.store, env.policy, record=True)
    assert len(env.store.list_health()) == 2


def test_clock_is_simulated_not_system(env):
    assert env.store.now().year == 2026 and env.store.now().month == 3
