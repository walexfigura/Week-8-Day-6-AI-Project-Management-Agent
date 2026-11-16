# Approval Policies

Implemented in `approvals/policies.py` (classification) and `approvals/requests.py` (enforcement).

## Automatic (no approval)
Read project records and task information; analyze dependencies; read authorized communication history; generate health assessments; draft messages; propose task updates; create internal follow-up records; prepare briefings.

## Requires human approval
Sending Slack messages; sending emails; changing task deadlines; reassigning owners; changing task status; adding/removing dependencies; moving milestones; updating official Monday.com records; project-wide announcements.

## Prohibited by default
Sending live messages; modifying live Monday.com boards; deleting tasks; changing permissions; making financial commitments; messaging unapproved recipients. Unknown action types are denied.

## How approval works
1. The agent creates a request containing the **exact** payload (message text + recipient + channel, or task changes + base version) and its SHA-256 hash.
2. A human runs `python run.py approve --request APR-...`, sees the exact action, and types `approve`. The approver name is recorded. The agent's own identity and reserved names (agent, model, assistant, system...) can't approve.
3. `python run.py apply-approved --request APR-...` executes in mock mode. The integration re-verifies status and hash before writing.
4. Results (message IDs, before/after values) are stored on the request. Re-running is idempotent.

An approval authorizes one action only. A different message, recipient or change needs a new approval.
