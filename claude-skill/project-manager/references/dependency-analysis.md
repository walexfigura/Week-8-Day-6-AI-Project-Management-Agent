# Dependency Analysis

Dependencies are finish-to-start: task X can't finish until task Y is Done.

## Steps
1. Build the list of edges by task ID. Check every ID exists and there are no cycles (A → B → C → A). Report invalid data instead of guessing.
2. For the task in question, list **direct dependents** (who is waiting right now) and **all downstream** tasks.
3. Do a simple forward pass in **working days** (Mon–Fri, skip holidays):
   - start = later of today or the next working day after all dependencies finish
   - finish = start + remaining working days − 1
   - an owner's estimate replaces the planned finish (label it "estimate")
   - an overdue task with no estimate: assume it finishes today at the earliest (label it "assumption")
4. Compare each projected finish with its due date (slack = working days between them).
5. For each milestone, report baseline vs revised slack.

## Interpret carefully
- Slack shrinking is a **risk**. Only a milestone projected past its date is a **slip**.
- Note partial progress: work that can continue without the dependency (layout with placeholder images, ad copy without final photos).
- Say this is a projection, not a full critical-path analysis (no resource constraints, no probabilistic durations).

## Sample-data answer (for checking yourself)
Photography (TASK-002) directly blocks TASK-003 (approve images), TASK-004 (landing page), TASK-007 (ad creatives); 9 tasks downstream. If photos land Wed 2026-03-11: TASK-003 → Thu 3/12 (1 day late), TASK-007 → Mon 3/16 (1 day late), landing page still makes 3/16, launch approval slack 1 → 0, launch (3/24) slack 2 → 1: **at risk, not slipping**.
