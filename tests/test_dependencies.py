from datetime import date

import pytest

from project_manager_agent.clock import BusinessCalendar
from project_manager_agent.dependencies.graph import (
    CycleError,
    DependencyGraph,
    InvalidReferenceError,
)
from project_manager_agent.dependencies.impact import analyze_impact


def test_photography_dependents(env):  # Test 2
    g = DependencyGraph.from_store(env.store)
    assert g.direct_dependents("TASK-002") == ["TASK-003", "TASK-004", "TASK-007"]
    assert {"TASK-011", "TASK-013", "TASK-014", "TASK-008"} <= set(g.transitive_dependents("TASK-002"))


def test_one_task_blocks_multiple_workstreams(env):  # Test 3
    g = DependencyGraph.from_store(env.store)
    tasks = {t["task_id"]: t for t in env.store.list_tasks()}
    groups = {tasks[t]["group_id"] for t in g.direct_dependents("TASK-002")}
    assert {"GRP-WEB", "GRP-ADS"} <= groups


def test_independent_tasks_not_downstream(env):
    g = DependencyGraph.from_store(env.store)
    down = set(g.transitive_dependents("TASK-002"))
    assert not {"TASK-006", "TASK-009", "TASK-010", "TASK-012"} & down


def test_transitive_dependencies(env):
    g = DependencyGraph.from_store(env.store)
    assert g.transitive_dependencies("TASK-014") == sorted(set(g.transitive_dependencies("TASK-014")))
    assert "TASK-002" in g.transitive_dependencies("TASK-014")
    assert g.path_exists("TASK-002", "TASK-014")


def test_cycle_detected():  # Test 11
    g = DependencyGraph(["A", "B", "C"], [("A", "B"), ("B", "C"), ("C", "A")])
    cycle = g.find_cycle()
    assert cycle and cycle[0] == cycle[-1]
    with pytest.raises(CycleError):
        g.topological_order()


def test_cycle_in_store_makes_project_blocked(env):
    from project_manager_agent.monitoring.project_health import assess_health
    env.store.add_dependency("TASK-002", "TASK-014")
    h = assess_health(env.store, env.policy)
    assert h["classification"] == "Blocked"
    assert "Circular dependency" in h["graph_error"]


def test_invalid_reference_detected():
    g = DependencyGraph(["A"], [("A", "ZZZ")])
    assert g.invalid_references()
    with pytest.raises(InvalidReferenceError):
        g.validate()


def test_all_dataset_dependencies_valid(env):
    DependencyGraph.from_store(env.store).validate()


def test_impact_of_wednesday_estimate(env):  # Test 10
    imp = analyze_impact(env.store, BusinessCalendar(env.policy), "TASK-002", date(2026, 3, 11))
    assert imp["direct_dependents"] == ["TASK-003", "TASK-004", "TASK-007"]
    late = {a["task_id"] for a in imp["affected_tasks"] if a["would_miss_due_date"]}
    assert late == {"TASK-003", "TASK-007"}
    launch = next(m for m in imp["milestones"] if m["milestone_id"] == "MS-LAUNCH")
    assert launch["projected_to_slip"] is False
    assert launch["baseline_slack_workdays"] == 2 and launch["revised_slack_workdays"] == 1
    assert imp["launch_projected_to_slip"] is False


def test_large_delay_does_slip_launch(env):
    imp = analyze_impact(env.store, BusinessCalendar(env.policy), "TASK-002", date(2026, 3, 18))
    assert imp["launch_projected_to_slip"] is True
