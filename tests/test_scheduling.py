from datetime import date, datetime
from zoneinfo import ZoneInfo

from project_manager_agent.clock import (
    BusinessCalendar,
    find_date_mentions,
    parse_dt,
    resolve_date_expression,
)
from project_manager_agent.dependencies.graph import DependencyGraph
from project_manager_agent.dependencies.scheduling import project_schedule

REF = parse_dt("2026-03-10T09:55:00-04:00")  # Tuesday


def cal():
    import json
    from pathlib import Path
    return BusinessCalendar(json.loads((Path(__file__).parents[1] / "config/policy.json").read_text()))


def test_weekday_resolution():
    assert resolve_date_expression("We'll have the final images on Wednesday.", REF)[0] == date(2026, 3, 11)
    assert resolve_date_expression("Should be done tomorrow.", REF)[0] == date(2026, 3, 11)
    assert resolve_date_expression("ready Monday 3/16, not Friday", REF)[0] == date(2026, 3, 16)
    assert resolve_date_expression("Board says the 16th but I'm planning on Wednesday the 18th", REF)[0] == date(2026, 3, 18)


def test_ranges_are_not_resolved():
    assert resolve_date_expression("Can we push it to next week?", REF) == (None, None)


def test_negated_dates_flagged():
    mentions = find_date_mentions("ready Monday 3/16, not Friday", REF)
    assert any(m["negated"] and m["date"] == "2026-03-13" for m in mentions)


def test_workdays_skip_weekends_and_holidays():
    c = cal()
    assert c.add_workdays(date(2026, 3, 13), 2) == date(2026, 3, 16)   # Fri + 1 workday = Mon
    assert not c.is_workday(date(2026, 4, 3))                           # Good Friday holiday
    assert c.workdays_between(date(2026, 3, 20), date(2026, 3, 24)) == 2


def test_working_hours():
    c = cal()
    tz = ZoneInfo("America/New_York")
    assert c.is_working_time(datetime(2026, 3, 10, 10, 0, tzinfo=tz))
    assert not c.is_working_time(datetime(2026, 3, 14, 10, 0, tzinfo=tz))   # Saturday
    assert c.next_working_time(datetime(2026, 3, 13, 18, 0, tzinfo=tz)) == datetime(2026, 3, 16, 9, 0, tzinfo=tz)
    assert c.add_working_hours(datetime(2026, 3, 10, 16, 0, tzinfo=tz), 8) == datetime(2026, 3, 11, 15, 30, tzinfo=tz)


def test_projection_baseline(env):
    g = DependencyGraph.from_store(env.store)
    tasks = {t["task_id"]: t for t in env.store.list_tasks()}
    p = project_schedule(g, tasks, date(2026, 3, 10), cal())
    assert p["TASK-002"].basis == "assumed_today_no_estimate"
    assert p["TASK-014"].projected_finish == "2026-03-20"
    assert p["TASK-014"].slack_workdays == 2


def test_projection_uses_estimate(env):
    g = DependencyGraph.from_store(env.store)
    tasks = {t["task_id"]: t for t in env.store.list_tasks()}
    p = project_schedule(g, tasks, date(2026, 3, 10), cal(), {"TASK-002": date(2026, 3, 11)})
    assert p["TASK-002"].basis == "owner_estimate"
    assert p["TASK-007"].projected_finish == "2026-03-16"
    assert p["TASK-014"].projected_finish == "2026-03-23"
