"""Command-line interface: `python run.py <command>`.

Offline (no API key): doctor, init, reset-demo, health, tasks, blockers, investigate --offline,
followups, approvals, approve, reject, apply-approved, simulate-reply --offline, report, history,
clock, exempt, eval, build-skill, demo --offline.
Model-backed (OPENAI_API_KEY): investigate, simulate-reply, demo (without --offline), eval --llm.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from typing import Any

from .actions import apply_approved
from .agent import AgentError, ProjectManagerAgent, offline_investigate
from .approvals.policies import CHANGE_FIELD_LABELS
from .approvals.requests import ApprovalError, decide
from .clock import BusinessCalendar, fmt_day, parse_dt
from .communications.followups import describe_followup, select_followups
from .config import MissingCredentialsError, Settings, load_settings
from .dependencies.graph import DependencyGraph
from .monitoring.project_health import assess_health
from .reporting.project_briefing import write_briefing
from .scenarios import ScenarioError, all_replies, apply_seed_followups, simulate_reply
from .storage import NotInitializedError, Store, open_store

BOLD, DIM, RED, GREEN, YELLOW, RESET = ("\033[1m", "\033[2m", "\033[31m", "\033[32m", "\033[33m", "\033[0m") \
    if sys.stdout.isatty() else ("",) * 6


def hr(title: str) -> None:
    print(f"\n{BOLD}{title}{RESET}\n" + "-" * min(len(title), 78))


def name_of(store: Store, mid: str | None) -> str:
    if not mid:
        return "-"
    m = store.get_member(mid) or store.get_contact(mid)
    return m["name"] if m else mid


# ---------------------------------------------------------------------------- setup
def cmd_doctor(settings: Settings, args: argparse.Namespace) -> int:
    ok = True

    def line(passed: bool, label: str, detail: str = "") -> None:
        nonlocal ok
        ok &= passed
        print(f"  [{GREEN + 'ok' + RESET if passed else RED + '!!' + RESET}] {label}{(' - ' + detail) if detail else ''}")

    hr("Environment")
    line(sys.version_info >= (3, 10), f"Python {sys.version.split()[0]}", "3.10+ required (tested on 3.11)")
    for mod in ("openai", "dotenv"):
        try:
            __import__(mod)
            line(True, f"package '{mod}' installed")
        except ImportError:
            line(False, f"package '{mod}' missing", "run: pip install -e .")
    try:
        __import__("matplotlib")
        print("  [ok] matplotlib installed (optional charts)")
    except ImportError:
        print("  [--] matplotlib not installed (optional; `report --charts` will skip charts)")
    hr("Files")
    for rel in ["agent/system-prompt.md", "agent/scope.md", "config/policy.json", "data/monday/tasks.json",
                "data/monday/dependencies.json", "data/slack/messages.json", "data/gmail/email-threads.json",
                "data/people/team-members.json", "knowledge-base/followup-rules.md"]:
        line((settings.project_root / rel).exists(), rel)
    workflows = sorted(p.name for p in settings.workflows_dir.glob("*.md"))
    line(len(workflows) >= 8, f"{len(workflows)} workflow documents")
    hr("Data integrity")
    try:
        tasks = json.loads((settings.data_dir / "monday/tasks.json").read_text())["tasks"]
        deps = json.loads((settings.data_dir / "monday/dependencies.json").read_text())["dependencies"]
        g = DependencyGraph([t["task_id"] for t in tasks], [(d["task_id"], d["depends_on"]) for d in deps])
        g.validate()
        line(True, f"{len(tasks)} tasks, {len(deps)} dependencies, all references valid, no cycles")
        owners = {m["member_id"] for m in json.loads((settings.data_dir / "people/team-members.json").read_text())["members"]}
        line(all(t["owner_id"] in owners for t in tasks), "every task owner exists in the team directory")
        emails = (settings.data_dir / "people/team-members.json").read_text()
        line(".example" in emails and "@gmail.com" not in emails, "contact data uses reserved .example domains")
    except Exception as exc:  # noqa: BLE001
        line(False, "dataset validation", str(exc))
    hr("State & credentials")
    store = open_store(settings)
    if store.initialized:
        print(f"  [ok] state initialized: scenario '{store.get_meta('scenario')}', simulated now {store.now().isoformat()}")
    else:
        print("  [--] state not initialized yet - run: python run.py init")
    print(f"  [{'ok' if settings.has_openai_key else '--'}] OPENAI_API_KEY {'set' if settings.has_openai_key else 'not set'}"
          f" (model: {settings.openai_model}); offline commands work either way")
    print(f"  [ok] approver name: {settings.approver}")
    print(f"\n{'All checks passed.' if ok else 'Some checks failed - see above.'}")
    return 0 if ok else 1


def init_state(settings: Settings, scenario: str) -> dict[str, Any]:
    store = open_store(settings)
    scen = store.load_dataset(settings.data_dir, scenario)
    seeded = apply_seed_followups(store, settings.load_policy(), scen)
    return {"scenario": scen["name"], "clock": scen["clock"], "seeded_followups": seeded, "description": scen["description"]}


def cmd_init(settings: Settings, args: argparse.Namespace) -> int:
    store = open_store(settings)
    if store.initialized and not args.force:
        print(f"State already exists (scenario '{store.get_meta('scenario')}', simulated now {store.now().isoformat()}).")
        print("Keeping it so you can resume. Use `python run.py reset-demo` (or `init --force`) to start over.")
        return 0
    info = init_state(settings, args.scenario or settings.scenario)
    print(f"Initialized scenario '{info['scenario']}'. Simulated clock: {info['clock']}.")
    print(info["description"])
    if info["seeded_followups"]:
        print(f"Scenario history: {len(info['seeded_followups'])} follow-up(s) were already sent before the scenario starts.")
    print("Next: python run.py health")
    return 0


def cmd_reset(settings: Settings, args: argparse.Namespace) -> int:
    info = init_state(settings, args.scenario or settings.scenario)
    print(f"Reset complete. Scenario '{info['scenario']}' restored; simulated clock {info['clock']}. All follow-ups, "
          "approvals, proposals and board changes from earlier runs were cleared.")
    return 0


# ---------------------------------------------------------------------------- monitoring
def print_health(store: Store, h: dict[str, Any]) -> None:
    color = {"On Track": GREEN, "At Risk": YELLOW, "Blocked": RED, "Needs Review": YELLOW}.get(h["classification"], "")
    print(f"{BOLD}{h['project_name']}{RESET}  as of {h['as_of']} (simulated)")
    print(f"Health: {color}{BOLD}{h['classification']}{RESET}")
    for level in ("Blocked", "At Risk", "Needs Review"):
        for r in h["reasons"].get(level, []):
            print(f"  - [{level}] {r}")
    if not h.get("metrics"):
        return
    m = h["metrics"]
    print(f"\nTasks: {m['total_tasks']} total · {m['completed_tasks']} done · {m['overdue_tasks']} overdue · "
          f"{m['blocked_tasks']} blocked · {m['waiting_tasks']} waiting · {m['tasks_at_risk']} at risk")
    print(f"Follow-ups unanswered: {m['unanswered_followups']} · approvals pending: {m['pending_approvals']} · "
          f"open decisions: {m['open_decisions']}")
    for ms in m["upcoming_milestones"]:
        print(f"Milestone {ms['name']}: due {ms['due_date']}, projected {ms['projected_finish']} "
              f"({ms['slack_workdays']} workday(s) slack)")
    print(f"{DIM}{h['method_note']}{RESET}")


def cmd_health(settings: Settings, args: argparse.Namespace) -> int:
    store = open_store(settings)
    h = assess_health(store, settings.load_policy(), record=True)
    if args.json:
        print(json.dumps({k: v for k, v in h.items() if k != "tasks"}, indent=2, default=str))
    else:
        print_health(store, h)
    return 0


def cmd_tasks(settings: Settings, args: argparse.Namespace) -> int:
    store = open_store(settings)
    policy = settings.load_policy()
    h = assess_health(store, policy)
    g = DependencyGraph.from_store(store)
    today = store.now().date()
    print(f"{'ID':9} {'Task':34} {'Owner':13} {'Status':12} {'Due':11} {'Proj.':11} Depends on")
    for t in store.list_tasks():
        p = h["projections"][t["task_id"]]
        late = date.fromisoformat(t["due_date"]) < today and t["status"] != "Done"
        due = f"{RED}{t['due_date']}{RESET}" if late else t["due_date"]
        print(f"{t['task_id']:9} {t['name'][:33]:34} {name_of(store, t['owner_id'])[:12]:13} {t['status']:12} {due:11}"
              f"{' ' if not late else ''} {p['projected_finish']:11} {', '.join(g.direct_dependencies(t['task_id'])) or '-'}")
    if args.graph:
        print("\n```mermaid\n" + g.mermaid({t["task_id"]: t for t in store.list_tasks()}) + "\n```")
    return 0


def cmd_blockers(settings: Settings, args: argparse.Namespace) -> int:
    store = open_store(settings)
    h = assess_health(store, settings.load_policy())
    g = DependencyGraph.from_store(store)
    tasks = {t["task_id"]: t for t in h["tasks"]}
    hr("Overdue tasks")
    for o in h["overdue"]:
        down = g.transitive_dependents(o["task_id"])
        print(f"  {o['task_id']} {o['name']} ({name_of(store, o['owner_id'])}) - {o['days_overdue']} day(s) overdue; "
              f"{len(g.direct_dependents(o['task_id']))} direct / {len(down)} total downstream")
    if not h["overdue"]:
        print("  none")
    hr("Bottlenecks (one task holding up several others)")
    for b in h["bottlenecks"]:
        print(f"  {b['task_id']} {b['name']} blocks:")
        for d in b["blocking"]:
            t = tasks[d]
            print(f"     -> {d} {t['name']} ({name_of(store, t['owner_id'])}, {t['status']})"
                  + (f"  [{t['partial_progress']}]" if t.get("partial_progress") else ""))
    if not h["bottlenecks"]:
        print("  none")
    hr("Blocked / waiting tasks and what they wait for")
    for t in h["tasks"]:
        if t["status"] in {"Blocked", "Waiting"}:
            ups = [u for u in g.direct_dependencies(t["task_id"]) if tasks[u]["status"] != "Done"]
            print(f"  {t['task_id']} {t['name']} ({t['status']}) waits on {', '.join(ups) or 'nothing on the board (check notes)'}")
    hr("Projected late (forward-pass projection)")
    for p in h["projected_late"]:
        print(f"  {p['task_id']} {p['name']}: projected {p['projected_finish']} vs due {p['due_date']} ({p['basis']})")
    if not h["projected_late"]:
        print("  none")
    if h["conflicts"]:
        hr("Conflicting information")
        for c in h["conflicts"]:
            print(f"  {c['task_id']}: board {c['board_due_date']} vs {c['source']} {c['reported_date']} ({c['kind']})")
    return 0


# ---------------------------------------------------------------------------- agent
def make_agent(settings: Settings, store: Store, policy: dict[str, Any]) -> ProjectManagerAgent:
    settings.require_openai_key()
    return ProjectManagerAgent(settings, store, policy)


def print_investigation(store: Store, r: dict[str, Any]) -> None:
    print(f"{DIM}mode: {r['mode']} · trace: {r['trace_id']}{RESET}")
    print(f"\n{BOLD}Summary:{RESET} {r['summary']}")
    if r.get("findings"):
        print(f"\n{BOLD}Findings{RESET}")
        for f in r["findings"][:8]:
            print(f"  - [{f['certainty']}] {f['task_id']}: {f['observation']}")
    if r.get("not_contacted"):
        print(f"\n{BOLD}Deliberately not contacted{RESET}")
        for n in r["not_contacted"]:
            print(f"  - {n['task_id']} ({name_of(store, n.get('person_id'))}): {n['reason']}")
    for label, key in (("Risks", "risks"), ("Uncertainties", "uncertainties"), ("Recommended next steps", "recommended_next_steps")):
        if r.get(key):
            print(f"\n{BOLD}{label}{RESET}")
            for x in r[key]:
                print(f"  - {x}")
    print(f"\n{BOLD}Follow-ups drafted (NOT sent){RESET}")
    if not r["followups_drafted"]:
        print("  none")
    for fid in r["followups_drafted"]:
        f = store.get_followup(fid)
        print(f"  {describe_followup(store, f)}\n    \"{f['draft']}\"\n    approval: {f['approval_id']}")


def cmd_investigate(settings: Settings, args: argparse.Namespace) -> int:
    store = open_store(settings)
    policy = settings.load_policy()
    if args.offline or not settings.has_openai_key:
        if not args.offline:
            print(f"{YELLOW}OPENAI_API_KEY not set - using the offline rules engine (no AI judgment).{RESET}")
        r = offline_investigate(store, policy, args.task)
    else:
        r = make_agent(settings, store, policy).investigate(args.task)
    print_investigation(store, r)
    return 0


def cmd_followups(settings: Settings, args: argparse.Namespace) -> int:
    store = open_store(settings)
    policy = settings.load_policy()
    hr("Follow-up plan (deterministic rules + priority score)")
    for p in select_followups(store, policy):
        rec = p["recommendation"].replace("_", " ")
        print(f"  {p['score']:5.1f}  {p['task_id']} {p['name'][:32]:32} {name_of(store, p['owner_id'])[:12]:12} -> {BOLD}{rec}{RESET}")
        print(f"         {DIM}{p['why']}{RESET}")
    hr("Follow-up records")
    fus = store.list_followups()
    if not fus:
        print("  none yet - run `python run.py investigate` to draft one")
    for f in fus:
        print(f"  {describe_followup(store, f)}  reply: {f['reply_status']}"
              + (f"  next allowed: {f['next_allowed_at']}" if f["next_allowed_at"] else ""))
        if f["current_commitment"]:
            print(f"      commitment: {f['current_commitment']}")
    return 0


def show_approval(store: Store, a: dict[str, Any]) -> None:
    p = a["payload"]
    print(f"{BOLD}{a['approval_id']}{RESET} [{a['status']}] {a['action_type']} · requested {a['requested_at']} by {a['requested_by']}")
    print(f"  Target : {a['target']}")
    print(f"  Reason : {a['reason']}")
    if a["evidence"]:
        print(f"  Evidence: {', '.join(a['evidence'])}")
    if a["action_type"] == "send_slack_message":
        print(f"  Channel: {p['channel_id']} (mock Slack DM){' in thread ' + p['thread_id'] if p.get('thread_id') else ''}")
        print(f"  Message (exact text):\n    \"{p['text']}\"")
    elif a["action_type"] == "send_email":
        print(f"  To: {p['to']}  Subject: {p['subject']}\n  Body (exact text):\n    \"{p['body']}\"")
    elif a["action_type"] == "update_task":
        task = store.get_task(p["task_id"]) or {}
        print(f"  Task: {p['task_id']} {task.get('name', '')} (proposed against version {p['base_version']}, now {task.get('version')})")
        for k, v in p["changes"].items():
            label = CHANGE_FIELD_LABELS.get(k, k)
            if k == "add_update":
                print(f"  - {label}:\n      \"{v}\"")
            else:
                print(f"  - {label}: {task.get(k)} -> {v}")
    print(f"  Payload hash: {a['payload_hash'][:16]}…")
    if a["decided_by"]:
        print(f"  Decision: {a['status']} by {a['decided_by']} at {a['decided_at']}")
    if a["execution_result"]:
        print(f"  Result: {json.dumps(a['execution_result'], default=str)}")


def cmd_approvals(settings: Settings, args: argparse.Namespace) -> int:
    store = open_store(settings)
    items = store.list_approvals(None if args.all else {"pending", "approved"})
    if not items:
        print("No pending or approved-but-unapplied requests." + ("" if args.all else " Use --all for history."))
    for a in items:
        show_approval(store, a)
        if a["status"] == "pending":
            print(f"  -> python run.py approve --request {a['approval_id']}   (or reject)")
        elif a["status"] == "approved":
            print(f"  -> python run.py apply-approved --request {a['approval_id']}")
        print()
    return 0


def confirm(prompt: str, assume_yes: bool) -> str:
    if assume_yes:
        return "approve"
    try:
        return input(prompt).strip().lower()
    except EOFError:
        return ""


def cmd_approve(settings: Settings, args: argparse.Namespace, *, approve: bool = True) -> int:
    store = open_store(settings)
    a = store.get_approval(args.request)
    if a is None:
        print(f"No approval request {args.request}. Run `python run.py approvals`.")
        return 1
    show_approval(store, a)
    approver = args.approver or settings.approver
    if approve:
        answer = confirm(f"\nType 'approve' to approve exactly this action as {approver}: ", args.yes)
        if answer != "approve":
            print("Not approved. Nothing changed.")
            return 1
    try:
        res = decide(store, args.request, approve=approve, approver=approver, note=args.note or "",
                     expected_hash=a["payload_hash"])
    except ApprovalError as exc:
        print(f"{RED}Refused:{RESET} {exc}")
        return 1
    print(f"{GREEN if approve else YELLOW}{res['approval_id']} {res['status']} by {res['decided_by']}.{RESET}")
    if approve:
        print(f"Approved, not yet executed. Apply it with: python run.py apply-approved --request {res['approval_id']}")
    return 0


def cmd_apply(settings: Settings, args: argparse.Namespace) -> int:
    store = open_store(settings)
    try:
        result = apply_approved(store, settings.load_policy(), args.request)
    except ApprovalError as exc:
        print(f"{RED}Refused:{RESET} {exc}")
        return 1
    except Exception as exc:  # noqa: BLE001 - integration failure: approval stays approved for a retry
        print(f"{RED}Integration failed:{RESET} {exc}. The approval is still valid; retry apply-approved.")
        return 1
    if result.get("deferred"):
        print(f"{YELLOW}Deferred:{RESET} {result['reason']} Scheduled for {result['scheduled_for']}.")
        return 0
    if result.get("already_executed"):
        print("Already executed earlier - nothing was done twice.")
    print(json.dumps(result, indent=2, default=str))
    return 0


def print_reply_result(store: Store, res: dict[str, Any]) -> None:
    if res.get("duplicate_event"):
        print(f"{YELLOW}Duplicate event{RESET}: {res['note']}")
        return
    print(f"Reply {res['message_id']}: \"{res['text']}\"")
    if res.get("status") != "processed":
        print(f"  {res.get('status')}: {res.get('note')}")
        return
    i, a = res["interpretation"], res["association"]
    print(f"  Sender: {name_of(store, res['sender_id'])} (verified directory identity)")
    print(f"  Task: {i['related_task_id']} via {a['method']} (confidence {a['confidence']})")
    print(f"  Intent: {BOLD}{i['message_intent']}{RESET} · interpreter: {i.get('interpreter')}")
    if i.get("new_estimated_completion"):
        print(f"  New estimated completion: {i['new_estimated_completion']} ({fmt_day(date.fromisoformat(i['new_estimated_completion']))}, "
              f"from '{i.get('date_expression')}')")
    if i.get("blocker"):
        print(f"  Blocker: {i['blocker']}")
    print(f"  Completion confirmed: {i['completion_confirmed']}  ·  requires approval: {i['requires_human_approval']}")
    for u in i.get("uncertainties", []):
        print(f"  ! {u}")
    if res.get("impact"):
        print(f"\n  {BOLD}Dependency impact:{RESET} {res['impact']['summary']}")
    if res.get("security_note"):
        print(f"  {YELLOW}{res['security_note']}{RESET}")
    print(f"\n  {BOLD}Created (all pending human approval):{RESET}")
    for act in res["actions"]:
        print(f"   - {act['type']}: " + ", ".join(f"{k}={v}" for k, v in act.items() if k not in {"type", "changes"}))


def cmd_simulate_reply(settings: Settings, args: argparse.Namespace) -> int:
    store = open_store(settings)
    policy = settings.load_policy()
    if args.list:
        for k, r in all_replies(settings.data_dir).items():
            print(f"  {k:28} [{r['scenario']}] {name_of(store, r['from_member_id'])}: \"{r['text']}\"")
        return 0
    if not args.scenario:
        print("Pass --scenario NAME (see --list).")
        return 1
    interpreter = None
    if not args.offline and settings.has_openai_key:
        interpreter = make_agent(settings, store, policy).interpret_reply
    elif not args.offline:
        print(f"{YELLOW}OPENAI_API_KEY not set - interpreting with the offline rules engine.{RESET}")
    try:
        results = simulate_reply(store, policy, settings.data_dir, args.scenario, interpreter=interpreter)
    except ScenarioError as exc:
        print(f"{RED}{exc}{RESET}")
        return 1
    for n, res in enumerate(results, 1):
        if len(results) > 1:
            hr(f"Delivery {n} of {len(results)}")
        print_reply_result(store, res)
    return 0


def cmd_report(settings: Settings, args: argparse.Namespace) -> int:
    store = open_store(settings)
    out = write_briefing(store, settings.load_policy(), settings.reports_dir, charts=args.charts)
    print(f"Report written: {out['path']} (also reports/latest.md). Classification: {out['classification']}.")
    if out["charts"]:
        print("Charts: " + ", ".join(out["charts"]))
    if out["chart_note"]:
        print(out["chart_note"])
    return 0


def cmd_history(settings: Settings, args: argparse.Namespace) -> int:
    store = open_store(settings)
    hr("Communication log (mock Slack + mailbox, agent-related)")
    for m in store.list_slack_messages():
        if m["direction"] != "history":
            arrow = "->" if m["direction"] == "outgoing" else "<-"
            who = "PM agent" if m["direction"] == "outgoing" else name_of(store, (store.member_by_slack_id(m["author_slack_id"]) or {}).get("member_id"))
            print(f"  {m['ts']} {arrow} [{m['channel_id']} thread {m['thread_id']}] {who}: \"{m['text']}\"")
    for e in store.list_email_messages():
        if e["direction"] != "history":
            print(f"  {e['sent_at']} -> email {e['to_addrs']}: {e['subject']}")
    hr("Board changes (approved)")
    for c in store.list_changes():
        print(f"  {c['applied_at']} {c['task_id']} via {c['approval_id']}: {c['before']} -> {c['after']}")
    hr("Commitments")
    for c in store.list_commitments():
        print(f"  {'(superseded) ' if c['superseded'] else ''}{c['task_id']} {name_of(store, c['member_id'])}: {c['kind']} "
              f"{c['commitment_date'] or ''} from {c['source_message_id']} [{c['confidence']}]")
    hr("Processed reply events")
    for e in store.list_processed_events():
        print(f"  {e['event_id']} ({e['message_id']}) at {e['processed_at']}: {e['result'].get('status')}")
    hr("Health history")
    for x in store.list_health():
        print(f"  {x['created_at']} {x['classification']}")
    return 0


def cmd_clock(settings: Settings, args: argparse.Namespace) -> int:
    store = open_store(settings)
    if args.advance_hours:
        store.advance_clock(args.advance_hours)
    if args.set:
        store.set_now(parse_dt(args.set))
    cal = BusinessCalendar(settings.load_policy())
    now = store.now()
    print(f"Simulated now: {now.isoformat()} ({now.strftime('%A')}) · working time: {cal.is_working_time(now)}")
    return 0


def cmd_exempt(settings: Settings, args: argparse.Namespace) -> int:
    store = open_store(settings)
    target_type, target_id = ("member", args.member) if args.member else ("task", args.task)
    if not target_id:
        print("Pass --member TM-... or --task TASK-...")
        return 1
    until = parse_dt(args.until) if "T" in args.until else parse_dt(args.until + "T23:59:00" + store.now().isoformat()[-6:])
    store.set_exemption(target_type, target_id, until.isoformat(), args.reason)
    print(f"No reminders for {target_type} {target_id} until {until.isoformat()} ({args.reason}).")
    return 0


def cmd_eval(settings: Settings, args: argparse.Namespace) -> int:
    sys.path.insert(0, str(settings.project_root))
    from evals.run_evals import main as eval_main
    return eval_main(["--llm"] if args.llm else [])


def cmd_build_skill(settings: Settings, args: argparse.Namespace) -> int:
    sys.path.insert(0, str(settings.project_root))
    from scripts.build_skill import main as build_main
    return build_main([])


def cmd_demo(settings: Settings, args: argparse.Namespace) -> int:
    from .demo import run_demo
    return run_demo(settings, offline=args.offline, interactive=not args.non_interactive)


# ---------------------------------------------------------------------------- parser
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python run.py", description="Northstar Supply AI Project Manager (educational, mock mode).")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor", help="Check Python, packages, data files and credentials")
    s = sub.add_parser("init", help="Load the fictional project into local state (keeps existing state)")
    s.add_argument("--scenario", help="launch-delay (default), missing-update, conflicting-deadlines, completed-task, duplicate-replies")
    s.add_argument("--force", action="store_true", help="Overwrite existing state")
    s = sub.add_parser("reset-demo", help="Restore the original fictional demonstration")
    s.add_argument("--scenario")
    s = sub.add_parser("health", help="Project health check")
    s.add_argument("--json", action="store_true")
    s = sub.add_parser("tasks", help="Tasks, owners, dates, projections and dependencies")
    s.add_argument("--graph", action="store_true", help="Also print a Mermaid dependency graph")
    sub.add_parser("blockers", help="Overdue tasks, bottlenecks and downstream impact")
    s = sub.add_parser("investigate", help="Agent investigation (model) - drafts follow-ups, never sends")
    s.add_argument("--task", help="Focus on one task, e.g. TASK-002")
    s.add_argument("--offline", action="store_true", help="Use the deterministic rules engine instead of the model")
    sub.add_parser("followups", help="Follow-up plan and follow-up records")
    s = sub.add_parser("approvals", help="Pending approval requests with the exact action")
    s.add_argument("--all", action="store_true", help="Include decided/executed history")
    for name, helptext in (("approve", "Approve one request (human only)"), ("reject", "Reject one request")):
        s = sub.add_parser(name, help=helptext)
        s.add_argument("--request", required=True)
        s.add_argument("--approver", help="Your name (default PM_APPROVER or OS user)")
        s.add_argument("--note")
        if name == "approve":
            s.add_argument("--yes", action="store_true", help="Skip the typed confirmation (you are still the approver)")
    s = sub.add_parser("apply-approved", help="Execute an approved request (mock send / mock board update)")
    s.add_argument("--request", required=True)
    s = sub.add_parser("simulate-reply", help="Deliver a fictional reply into a sent follow-up's thread and process it")
    s.add_argument("--scenario", help="Reply name, e.g. photography_delayed")
    s.add_argument("--list", action="store_true")
    s.add_argument("--offline", action="store_true", help="Interpret with rules instead of the model")
    s = sub.add_parser("report", help="Write a Markdown project health briefing to reports/")
    s.add_argument("--charts", action="store_true", help="Also render PNG charts (needs matplotlib)")
    sub.add_parser("history", help="Communication log, board changes, commitments, events")
    s = sub.add_parser("clock", help="Show or move the simulated clock")
    s.add_argument("--advance-hours", type=float)
    s.add_argument("--set", help="ISO timestamp with offset")
    s = sub.add_parser("exempt", help="Pause reminders for a person or task")
    s.add_argument("--member")
    s.add_argument("--task")
    s.add_argument("--until", required=True, help="YYYY-MM-DD or ISO timestamp")
    s.add_argument("--reason", default="manual exemption")
    s = sub.add_parser("eval", help="Run evaluations (deterministic; --llm adds model-based cases)")
    s.add_argument("--llm", action="store_true")
    sub.add_parser("build-skill", help="Build dist/ai-project-manager-skill.zip")
    s = sub.add_parser("demo", help="Guided 14-stage walkthrough (resumable)")
    s.add_argument("--offline", action="store_true", help="Rules engine instead of the model")
    s.add_argument("--non-interactive", action="store_true", help="Stop at each approval gate instead of prompting")
    return p


COMMANDS = {
    "doctor": cmd_doctor, "init": cmd_init, "reset-demo": cmd_reset, "health": cmd_health, "tasks": cmd_tasks,
    "blockers": cmd_blockers, "investigate": cmd_investigate, "followups": cmd_followups, "approvals": cmd_approvals,
    "approve": cmd_approve, "reject": lambda s, a: cmd_approve(s, a, approve=False), "apply-approved": cmd_apply,
    "simulate-reply": cmd_simulate_reply, "report": cmd_report, "history": cmd_history, "clock": cmd_clock,
    "exempt": cmd_exempt, "eval": cmd_eval, "build-skill": cmd_build_skill, "demo": cmd_demo,
}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = load_settings()
    try:
        return COMMANDS[args.command](settings, args)
    except NotInitializedError as exc:
        print(f"{exc}")
        return 1
    except MissingCredentialsError as exc:
        print(f"{RED}{exc}{RESET}")
        return 2
    except AgentError as exc:
        print(f"{RED}Agent error:{RESET} {exc}")
        return 1
    except KeyError as exc:
        print(f"{RED}Not found:{RESET} {exc}")
        return 1
