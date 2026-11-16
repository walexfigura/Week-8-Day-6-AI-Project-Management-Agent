# Tool Configuration (Mode B)

Connector and tool names differ by environment. Discover what's available, map it to these capabilities, and tell the user what's missing.

| Capability | Monday.com | Slack | Gmail |
|---|---|---|---|
| Read tasks/items, columns, updates | board/item read tools | - | - |
| Read conversations | - | channel/thread read or search | thread read/search |
| Draft | (propose in chat) | draft tool if available, else draft in chat | create draft |
| Send / write | item update tools | send message tool | send tool |

## Monday.com
- Boards differ. Identify which columns hold status, owner (people), due date (date/timeline) and dependencies (dependency column) **before** analyzing. Ask the user if unclear.
- Status labels are custom ("Working on it", "Stuck"). Map them to the standard statuses and show the mapping.
- Write status/date/owner changes only after approval; post an item update explaining the change.

## Slack
- Search the project channel and DMs for task names/IDs before drafting.
- Identify people by Slack user ID, not display name.
- Reply in the existing thread when following up on a thread.

## Gmail
- Read only threads relevant to the project. Reply within the thread (same subject).
- Prefer creating a **draft** for the user to review. Send only after explicit approval.

## If tools are missing
Say which capability is unavailable and fall back to drafting text for the user to send or paste. Never claim an action happened without a tool result.

## State across sessions
Connected systems hold official records, not your follow-up history. Offer the follow-up log (`followup-workflows.md`) or post a Monday.com update (with approval) so the next session can see what was asked.
