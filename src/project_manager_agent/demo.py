"""Guided, resumable walkthrough of the photography-delay scenario (all 14 stages).

Every stage calls the same code the individual CLI commands use - nothing is
scripted output. The demo works out where you are from the persisted state, so
you can quit at any approval gate and run `python run.py demo` again later
(Stage 14: resume).
"""

from __future__ import annotations

from typing import Any

from .actions import apply_approved
from .agent import ProjectManagerAgent, offline_investigate
from .approvals.requests import ApprovalError, decide
from .cli import (
    BOLD,
    DIM,
    GREEN,
    RESET,
    YELLOW,
    confirm,
    init_state,
    name_of,
    print_health,
    print_investigation,
    print_reply_result,
    show_approval,
)
from .config import Settings
from .dependencies.graph import DependencyGraph
from .monitoring.project_health import assess_health
from .reporting.project_briefing import write_briefing
from .scenarios import simulate_reply
from .storage import Store, open_store

TASK, OWNER, REPLY = "TASK-002", "TM-SARAH", "photography_delayed"


def stage(n: int, title: str) -> None:
    print(f"\n{BOLD}=== Stage {n}: {title} ==={RESET}")


def _sarah_followup(store: Store) -> dict[str, Any] | None:
    fus = [f for f in store.list_followups(task_id=TASK, recipient_id=OWNER) if f["purpose"] == "status_check"]
    return fus[-1] if fus else None


def _gate(store: Store, settings: Settings, approval_ids: list[str], interactive: bool) -> bool:
    """Human approval gate. Returns True if every request was decided and applied."""
    policy = settings.load_policy()
    for aid in approval_ids:
        a = store.get_approval(aid)
        if a["status"] == "executed":
            continue
        if a["status"] == "pending":
            print()
            show_approval(store, a)
            if not interactive:
                print(f"\n{YELLOW}Waiting for a human.{RESET} Approve with:\n  python run.py approve --request {aid}\n"
                      f"  python run.py apply-approved --request {aid}\nthen run `python run.py demo` again to continue.")
                return False
            answer = confirm(f"Type 'approve', 'reject', or press Enter to stop here (approver: {settings.approver}): ", False)
            if answer not in {"approve", "reject"}:
                print("Stopped at the approval gate. State is saved - run `python run.py demo` to resume.")
                return False
            try:
                decide(store, aid, approve=answer == "approve", approver=settings.approver, expected_hash=a["payload_hash"])
            except ApprovalError as exc:
                print(f"Refused: {exc}")
                return False
            a = store.get_approval(aid)
            print(f"{a['approval_id']} {a['status']} by {a['decided_by']}.")
        if a["status"] == "approved":
            result = apply_approved(store, policy, aid)
            if result.get("deferred"):
                print(f"{YELLOW}Deferred:{RESET} {result['reason']}")
                return False
            print(f"{GREEN}Executed (mock):{RESET} {a['action_type']} -> "
                  + (f"message {result.get('message_id')} in {result.get('channel_id')}" if "message_id" in result
                     else f"{result.get('task_id')} {result.get('before')} -> {result.get('after')}"))
    return True


def run_demo(settings: Settings, *, offline: bool, interactive: bool) -> int:
    use_model = settings.has_openai_key and not offline
    print(f"{BOLD}Northstar Supply AI Project Manager - photography delay walkthrough{RESET}")
    print(f"{DIM}Mode: {'model ' + settings.openai_model if use_model else 'offline rules engine (no model calls)'}. "
          f"All Slack/Gmail/Monday.com actions are simulated locally.{RESET}")
    store = open_store(settings)
    policy = settings.load_policy()
    if not store.initialized:
        init_state(settings, "launch-delay")
        print("Initialized the launch-delay scenario.")
    elif store.get_meta("scenario") != "launch-delay":
        print(f"Current state is scenario '{store.get_meta('scenario')}'. Run `python run.py reset-demo` first.")
        return 1
    agent = ProjectManagerAgent(settings, store, policy) if use_model else None
    fu = _sarah_followup(store)

    if fu is None:
        stage(1, "Scan Monday.com")
        h = assess_health(store, policy, record=True)
        print_health(store, h)
        g = DependencyGraph.from_store(store)
        print(f"\nOverdue: {', '.join(o['task_id'] + ' ' + o['name'] for o in h['overdue'])}")
        print(f"{TASK} directly blocks: {', '.join(g.direct_dependents(TASK))}; downstream total: {len(g.transitive_dependents(TASK))}")
        stage(2, "Investigate (updates, Slack, email)")
        stage(3, "Prioritize")
        stage(4, "Draft the message")
        r = agent.investigate() if agent else offline_investigate(store, policy)
        print_investigation(store, r)
        fu = _sarah_followup(store)
        if fu is None:
            print(f"{YELLOW}The investigation did not draft a check-in with Sarah about {TASK}. This walkthrough needs that "
                  f"follow-up, so it now drafts one with the offline rules engine (labeled as such).{RESET}")
            offline_investigate(store, policy, TASK)
            fu = _sarah_followup(store)
            if fu is None:
                print("Follow-up rules refused a draft (see `python run.py followups`). Stopping.")
                return 1
    else:
        print(f"\n{DIM}Resuming from saved state (Stage 14). Simulated now: {store.now().isoformat()}.{RESET}")

    if fu["status"] in {"pending_approval", "approved"}:
        stage(5, "Human approval - exact message and recipient")
        stage(6, "Simulated send")
        if not _gate(store, settings, [fu["approval_id"]], interactive):
            return 0
        fu = store.get_followup(fu["followup_id"])

    if fu["status"] == "sent":
        stage(7, "Simulated reply from Sarah")
        stage(8, "Interpret the response")
        stage(9, "Reassess dependencies")
        stage(10, "Propose updates and downstream notifications")
        interpreter = agent.interpret_reply if agent else None
        res = simulate_reply(store, policy, settings.data_dir, REPLY, interpreter=interpreter)[0]
        print_reply_result(store, res)
        fu = store.get_followup(fu["followup_id"])

    proposals = [p for p in store.list_proposals(TASK) if p["source_message_id"]]
    notices = [f for f in store.list_followups() if f["purpose"] == "downstream_notice"]
    pending = [p["approval_id"] for p in proposals] + [f["approval_id"] for f in notices]
    if pending and any(store.get_approval(a)["status"] != "executed" for a in pending):
        stage(11, "Human approval - task update and notifications")
        print(f"{len(pending)} action(s) need your decision: the Monday.com update for {TASK} and "
              f"{len(notices)} downstream notice(s) to {', '.join(name_of(store, f['recipient_id']) for f in notices)}.")
        if not _gate(store, settings, pending, interactive):
            return 0
        stage(12, "Persist the changes")
        t = store.get_task(TASK)
        print(f"Monday.com (mock) now shows {TASK}: status {t['status']}, due {t['due_date']}, version {t['version']}.")
        print(f"Board change log entries: {len(store.list_changes())}. Approval history: `python run.py approvals --all`.")

    stage(13, "Generate a briefing")
    out = write_briefing(store, policy, settings.reports_dir)
    print(f"Report: {out['path']} - classification {out['classification']}.")
    h = assess_health(store, policy)
    print_health(store, h)
    stage(14, "Resume later")
    print("Everything above is saved in state/northstar_pm.db. Close the terminal, come back, and run any command - "
          "nothing resets unless you run `python run.py reset-demo`.\nTry: python run.py history  |  python run.py followups")
    return 0
