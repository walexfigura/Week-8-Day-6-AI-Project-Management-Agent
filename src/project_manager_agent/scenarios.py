"""Scenario loading: overlays at init time, and simulated incoming replies.

`simulate-reply` plays the part of Slack: it delivers a fictional person's reply
event into the thread of the follow-up we actually sent them, then hands it to
the normal reply-processing pipeline (exactly what a webhook handler would do).
"""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from typing import Any

from .actions import build_executors
from .approvals.requests import create_request, decide, execute
from .clock import parse_dt
from .communications.reply_processing import Interpreter, process_incoming_reply
from .integrations.mock_slack import MockSlack
from .storage import Store, new_id


class ScenarioError(RuntimeError):
    pass


def all_replies(data_dir: Path) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for path in sorted((data_dir / "scenarios").glob("*/scenario.json")):
        scen = json.loads(path.read_text(encoding="utf-8"))
        for key, reply in scen.get("replies", {}).items():
            out[key] = {**reply, "scenario": scen["name"], "key": key}
    return out


def apply_seed_followups(store: Store, policy: dict[str, Any], scenario: dict[str, Any]) -> list[str]:
    """Scenario overlay: follow-ups that were already sent before the scenario starts.

    They still go through a real approval record (approved by 'scenario-setup') so the
    audit trail stays consistent - nothing is ever marked sent without one.
    """
    created = []
    seeds = scenario.get("overlays", {}).get("seed_followups", [])
    if not seeds:
        return created
    real_now = store.now()
    slack = MockSlack(store)
    for s in seeds:
        store.set_now(parse_dt(s["sent_at"]))
        member = store.get_member(s["recipient_id"])
        fid = new_id("FU")
        payload = {"followup_id": fid, "task_id": s["task_id"], "recipient_id": s["recipient_id"],
                   "channel_id": slack.dm_channel_for(member), "text": s["text"], "thread_id": None}
        store.insert_followup({"followup_id": fid, "task_id": s["task_id"], "recipient_id": s["recipient_id"],
                               "channel": "slack", "purpose": "status_check", "question_key": f"status_check:{s['task_id']}",
                               "draft": s["text"], "reason": "Scenario seed: sent before the scenario starts.",
                               "status": "pending_approval", "created_at": s["sent_at"], "created_by": "scenario-setup"})
        ap = create_request(store, action_type="send_slack_message", payload=payload, target=f"Slack DM to {member['name']}",
                            reason="Scenario seed", requested_by="scenario-setup")
        store.update_followup(fid, approval_id=ap["approval_id"])
        decide(store, ap["approval_id"], approve=True, approver="scenario-seed-human", note="Pre-approved scenario history")
        execute(store, ap["approval_id"], build_executors(store, policy, slack=slack, enforce_working_hours=False))
        created.append(fid)
    store.set_now(real_now)
    return created


def simulate_reply(store: Store, policy: dict[str, Any], data_dir: Path, key: str, *,
                   interpreter: Interpreter | None = None) -> list[dict[str, Any]]:
    replies = all_replies(data_dir)
    if key not in replies:
        raise ScenarioError(f"Unknown reply scenario {key!r}. Available: {', '.join(sorted(replies))}")
    r = replies[key]
    member = store.get_member(r["from_member_id"])
    if member is None:
        raise ScenarioError(f"Unknown member {r['from_member_id']}")
    sent = [f for f in store.list_followups(task_id=r["expects_followup_for"], recipient_id=member["member_id"])
            if f["sent_at"] and f["thread_id"]]
    if not sent:
        raise ScenarioError(
            f"No follow-up to {member['name']} about {r['expects_followup_for']} has been sent yet, so there is nothing for this "
            "reply to answer. Draft it (investigate), approve it, and apply it first - or run `python run.py demo`.")
    fu = sent[-1]
    reply_at = max(store.now(), parse_dt(fu["sent_at"]) + timedelta(minutes=r.get("minutes_after_send", 20)))
    if reply_at > store.now():
        store.set_now(reply_at)
    slack = MockSlack(store)
    results = []
    for _ in range(int(r.get("deliveries", 1))):
        msg, dup_delivery = slack.inject_incoming(member_id=member["member_id"], text=r["text"],
                                                  channel_id=slack.dm_channel_for(member), thread_id=fu["thread_id"],
                                                  event_id=r["event_id"], ts=reply_at)
        res = process_incoming_reply(store, policy, msg["message_id"], event_id=r["event_id"], interpreter=interpreter)
        res["delivery_was_duplicate"] = dup_delivery
        res["expected"] = r.get("expected")
        results.append(res)
    return results
