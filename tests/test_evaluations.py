"""Runs the deterministic evaluation suite (evals/run_evals.py) and requires every case to pass."""

from evals.run_evals import run_deterministic


def test_deterministic_eval_suite(tmp_path):
    results = run_deterministic(tmp_path)
    failed = [r for r in results if not r["passed"]]
    assert not failed, failed
    assert len(results) >= 23


def test_briefing_reflects_persisted_state(env):  # Test 22
    from project_manager_agent.reporting.project_briefing import build_briefing
    env.send_sarah()
    res = env.reply("photography_delayed")[0]
    prop = next(a for a in res["actions"] if a["type"] == "task_update_proposed")
    text, _ = build_briefing(env.store, env.policy)
    assert prop["approval_id"] in text and "| TASK-002 | Complete product photography | Sarah Patel | In Progress | 2026-03-09 |" in text
    env.approve_and_apply(prop["approval_id"])
    text, h = build_briefing(env.store, env.policy)
    assert "| TASK-002 | Complete product photography | Sarah Patel | In Progress | 2026-03-11 |" in text
    assert "Owner estimate Wed Mar 11" in text
    assert "Board change on TASK-002" in text
    assert "Done | 1" not in text or h["metrics"]["completed_tasks"] == 1


def test_agent_loop_with_scripted_model(env):
    """Exercises the real tool-calling loop with a fake OpenAI client (no network)."""
    import json
    from types import SimpleNamespace as NS

    from project_manager_agent.agent import ProjectManagerAgent

    class Call(NS):
        def model_dump(self, exclude_none=True):
            return {"type": "function_call", "name": self.name, "arguments": self.arguments, "call_id": self.call_id}

    msg = ("Hey Sarah! Checking in on the product photos. James and Kevin are waiting on them. I saw Lumen's note - "
           "what's the new date?")
    script = [
        [Call(type="function_call", name="get_project_health", arguments=json.dumps({"project_id": "PRJ-TRAILMATE"}), call_id="c1")],
        [Call(type="function_call", name="get_recent_messages_for_task", arguments=json.dumps({"task_id": "TASK-002"}), call_id="c2"),
         Call(type="function_call", name="send_slack_message", arguments="{}", call_id="c2b")],
        [Call(type="function_call", name="draft_slack_message", arguments=json.dumps({
            "recipient_id": "TM-SARAH", "task_id": "TASK-002", "message": msg, "purpose": "status_check",
            "reason": "Shared blocker for TASK-004 and TASK-007", "evidence_ids": ["GM-3001-1"], "thread_id": None}), call_id="c3")],
    ]
    state = {"i": 0, "drafted": None}

    class Responses:
        def create(self, **kw):
            assert kw["tool_choice"] == "required"
            assert not any(t["name"].startswith("send_") for t in kw["tools"])
            i = state["i"]
            state["i"] += 1
            if i < len(script):
                return NS(output=script[i], usage=NS(input_tokens=10, output_tokens=5))
            outputs = [x for x in kw["input"] if isinstance(x, dict) and x.get("type") == "function_call_output"]
            fid = json.loads(outputs[-1]["output"])["followup_id"]
            sub = {"summary": "Sarah's photos block two workstreams.", "focus_task_ids": ["TASK-002"], "findings": [],
                   "followups_drafted": [fid], "not_contacted": [{"task_id": "TASK-004", "person_id": "TM-JAMES",
                                                                   "reason": "covered by TASK-002"}],
                   "risks": [], "uncertainties": [], "recommended_next_steps": []}
            return NS(output=[Call(type="function_call", name="submit_investigation", arguments=json.dumps(sub), call_id="c4")],
                      usage=NS(input_tokens=10, output_tokens=5))

    agent = ProjectManagerAgent(env.settings, env.store, env.policy, client=NS(responses=Responses()))
    r = agent.investigate()
    fu = env.store.get_followup(r["followups_drafted"][0])
    assert fu["status"] == "pending_approval" and fu["draft"] == msg
    assert agent.usage["tool_calls"] == 4
    assert not [m for m in env.store.list_slack_messages() if m["direction"] == "outgoing"]
