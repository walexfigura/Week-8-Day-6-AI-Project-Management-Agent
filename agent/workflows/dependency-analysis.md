# Workflow: Dependency analysis

Edges mean "task X cannot finish until task Y is Done" (finish-to-start).

1. `get_task_dependencies(task_id)` gives direct and transitive upstream, direct dependents and all downstream tasks.
2. `analyze_schedule_impact(task_id, new_finish_date)` runs a forward-pass projection over working days and reports, per downstream task, baseline vs revised projected finish, slack and whether its own due date would be missed, plus each milestone's slack before and after.
3. Interpret, don't recompute. Points to make:
   - Which tasks depend on it **directly** (who is actually waiting).
   - Whether any downstream task can make **partial progress** (see `partial_progress`).
   - Whether a **milestone** is projected to slip (`projected_to_slip`) or only loses slack.
   - "Projected late" is a projection with a stated basis, not a fact.
4. A delay that consumes slack is a **risk**. Only a projected milestone slip is a **launch delay**, and moving a milestone is a management decision.
5. Limitations: no resource levelling, no probabilistic durations, no partial-overlap modelling. It is not a full critical-path method. Say so if asked.
