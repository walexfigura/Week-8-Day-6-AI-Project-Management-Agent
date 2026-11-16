# Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `ModuleNotFoundError: project_manager_agent` | Running `python -m` from the wrong folder, or package not installed | Run commands from the repo root with `python run.py …`, or `pip install -e .` |
| `ModuleNotFoundError: openai` / `dotenv` | Dependencies not installed in the active environment | Activate your venv, then `pip install -e ".[dev]"` |
| `python: command not found` (macOS/Linux) | Python 3 is installed as `python3` | Use `python3`, or activate the venv (it provides `python`) |
| Windows: `Activate.ps1 cannot be loaded` | PowerShell execution policy | `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`, or use `.venv\Scripts\activate.bat` in cmd |
| `OPENAI_API_KEY is not set` | No `.env` or empty key | `cp .env.example .env`, add the key; or use `--offline` |
| `401` / `invalid_api_key` | Wrong or revoked key | Create a new key; check for spaces/quotes in `.env` |
| `model_not_found` / tool errors from the API | Model doesn't support Responses API function calling | Set `OPENAI_MODEL` to a current tool-calling model; clear `OPENAI_REASONING_EFFORT` for non-reasoning models |
| `Iteration limit … reached` / `Tool-call limit` | The model looped | Raise `PM_MAX_ITERATIONS` / `PM_MAX_TOOL_CALLS` slightly, or narrow scope with `--task` |
| `The mock project is not initialized` | No state yet | `python run.py init` |
| `Missing mock data file` / `Unknown scenario` | Edited/renamed files under `data/` | `git checkout data/`, or fix the path; `python run.py doctor` validates the dataset |
| `Circular dependency: …` / health shows **Blocked** | A dependency loop (often from an exercise) | Remove the edge in `data/monday/dependencies.json`, then `python run.py reset-demo` |
| Task shows a status you didn't expect | An approved change applied it, or you edited data without resetting | `python run.py history` shows board changes and their approvals; `reset-demo` restores the original |
| "Conflict" listed in health | Slack/email states a different date than the board | Expected behavior: ask the owner, then approve a proposal |
| `already queued as FU-…` / follow-up refused | Duplicate or spacing rule | Check `python run.py followups`; advance the clock (`clock --advance-hours 8`) to test spacing |
| `Deferred: Outside working hours` | Simulated time is evening/weekend | `python run.py clock --advance-hours N` into working hours, then `apply-approved` again |
| `Refused: … was approved for a different payload` | Payload changed after approval | Expected safety behavior. Create a new draft/proposal and approve that |
| `Refused: 'agent' cannot approve` | Reserved approver name | Use your own name: `--approver "Your Name"` or set `PM_APPROVER` |
| `changed since this was proposed (version …)` | The board changed between proposal and approval | Re-run the analysis to create a fresh proposal |
| `No follow-up to Sarah … has been sent yet` (simulate-reply) | Reply needs a sent follow-up to answer | Investigate → approve → apply-approved first, or run `python run.py demo` |
| Dates look wrong (e.g. "Wednesday" → wrong day) | Your system date isn't used; the scenario clock is | `python run.py clock` shows simulated now; `reset-demo` restores it |
| Charts missing | matplotlib not installed | `pip install -e ".[charts]"` (optional) |
| Want a clean slate | - | `python run.py reset-demo` (or delete `state/northstar_pm.db`) |
