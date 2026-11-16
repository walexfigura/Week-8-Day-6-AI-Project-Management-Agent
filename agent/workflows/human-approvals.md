# Workflow: Human approvals

| Action | Who can do it |
|---|---|
| Read tasks, messages, knowledge base; analyze dependencies; assess health | Agent, automatically |
| Draft Slack/email messages; propose task updates; create follow-up records; prepare briefings | Agent, automatically (creates an approval request) |
| Send Slack message / email | Human approval, then `apply-approved` |
| Change status, deadline, owner, dependencies; move milestones; announcements | Human approval, then `apply-approved` |
| Live sends, live board edits, deleting tasks, permission changes, financial commitments, unapproved recipients | Prohibited |

Enforcement lives in code: each approval stores the exact payload and its SHA-256 hash. The executor re-hashes before acting, so approving message A can never send message B or reach a different person. The requester (the agent) can't approve. Names like "agent" and "model" are rejected.

In your summaries: "Drafted a Slack message to Sarah (approval APR-...)". Never "messaged Sarah".
