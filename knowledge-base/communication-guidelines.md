# Communication Guidelines

Messages are **friendly, direct, professional, concise, specific to the task, and respectful of people's time.**

## Do
- Use the first name: "Hey Sarah!"
- Name the thing in plain words: "the product photos", not "TASK-002 deliverable".
- Say why it matters now: "James and Kevin are waiting on them."
- Show you've done your homework: "I saw Lumen's note about rescheduling."
- Ask one clear question that yields a date or decision.
- Label estimates honestly: "That's an estimate, not confirmed yet."

## Don't
- "Dear valued team member, I hope this message finds you well." (rejected automatically)
- Guilt or pressure ("per my last message", "this is the third time I've asked").
- Claim something happened that hasn't ("I've updated the board").
- Include contact details, budgets or anything the recipient doesn't need.
- Message several people about the same blocker when one answer resolves it.

## Good examples
> Hey Sarah! Checking in on the product photos. James's landing page and Kevin's ad creatives are waiting on them. Are we still expecting them today, or has the timeline changed?

> Heads up James - Sarah's final product photos are now expected Wed Mar 11 (photographer had to reschedule). That's an estimate, not confirmed yet. Can the landing page still hit Mon Mar 16 if the images land then?

## Channels
- **Slack DM:** quick internal check-ins (default for team members).
- **Email:** external contacts, or formal topics: vendor, contract, budget, external (`config/policy.json` → `channels.formal_topics`).

## Automatic checks (`communications/message_drafting.py::lint_message`)
Max `messages.max_length_chars` (600) characters; banned phrases from `messages.banned_phrases`; must address the recipient by first name near the start; must not claim completed actions.
