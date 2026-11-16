# Setup

## Install the Skill
1. Get `ai-project-manager-skill.zip` (in the repo's `dist/` folder, or build it with `python run.py build-skill`).
2. In Claude, open **Settings → Capabilities**, make sure Skills (and code execution, if required by your plan) are enabled, and upload the ZIP under Skills. Exact menu names can change; see Anthropic's current help docs if they differ.
3. Start a new conversation. The Skill loads when your request matches its description.

## Mode A: fictional sample project (no connectors)
Files in `assets/sample-data/`:
- `monday-board.json`: TrailMate launch board: 15 tasks, owners, statuses, due dates, priorities, dependencies, milestones, recent task updates. `as_of` is the simulated "now" (Tue 2026-03-10 09:30 ET).
- `slack-messages.json`: launch channel conversation.
- `email-threads.json`: shared mailbox (photographer reschedule, vendor question, contractor update, unanswered agency email).
- `team.json`: five team members, one off-project "Sarah", and approved external contacts. All fictional, `.example` domains.

Try:
- "Review our project and tell me which tasks need attention."
- "Which person should we follow up with first?"
- "Draft that follow-up."
- "Sarah replied: 'The photographer had to reschedule. We'll have the final images on Wednesday.' What changes?"
- "Write the project health report."

In Mode A nothing is sent and no board changes. When the user "approves", say clearly that this is a simulation and show what would have been sent or changed.

## Mode B: connected tools
See `tool-configuration.md`.

## Customize
Edit `communication-rules.md` (tone, channels), `followup-workflows.md` (spacing, limits), `approval-rules.md`, and `task-status-definitions.md` to match your team. Replace the sample data with an export of your own board.

## Test it
Run the prompts above and check that the Skill:
- picks Sarah (photography) first and does **not** message James and Kevin separately,
- skips inventory (fresh update yesterday),
- resolves "Wednesday" to 2026-03-11 and calls it an estimate,
- says the launch loses slack but is not projected to slip,
- asks for approval before "sending".
