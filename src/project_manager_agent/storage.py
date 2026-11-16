"""SQLite persistence for the mock systems *and* the agent's own state.

One database file (state/northstar_pm.db), two clearly separated groups of tables:

* ``monday_*``, ``slack_*``, ``gmail_*``, ``people_*`` - the **simulated external systems**.
  In a real deployment these live in Monday.com, Slack and Gmail; here they are local so
  the demo can show a real, persisted change after approval. Monday.com stays the
  source of truth for official task records.
* ``agent_*`` - what the agent itself remembers between runs: follow-ups, approvals,
  proposals, commitments, blockers, processed events, investigations and health history.

Only the integration classes and the approval executor write to ``monday_*`` and
outgoing ``slack_*``/``gmail_*`` rows. The model never gets a tool that does.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterator

from .clock import parse_dt

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS people_members (member_id TEXT PRIMARY KEY, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS people_contacts (contact_id TEXT PRIMARY KEY, data TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS monday_projects (project_id TEXT PRIMARY KEY, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS monday_tasks (
    task_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, data TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS monday_dependencies (
    task_id TEXT NOT NULL, depends_on TEXT NOT NULL, PRIMARY KEY (task_id, depends_on));
CREATE TABLE IF NOT EXISTS monday_updates (
    update_id TEXT PRIMARY KEY, task_id TEXT NOT NULL, author_id TEXT NOT NULL,
    created_at TEXT NOT NULL, body TEXT NOT NULL, source TEXT NOT NULL DEFAULT 'board');
CREATE TABLE IF NOT EXISTS monday_change_log (
    change_id TEXT PRIMARY KEY, task_id TEXT NOT NULL, approval_id TEXT NOT NULL,
    before TEXT NOT NULL, after TEXT NOT NULL, applied_at TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS slack_channels (channel_id TEXT PRIMARY KEY, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS slack_messages (
    message_id TEXT PRIMARY KEY, channel_id TEXT NOT NULL, thread_id TEXT, author_slack_id TEXT NOT NULL,
    ts TEXT NOT NULL, text TEXT NOT NULL, task_refs TEXT NOT NULL DEFAULT '[]',
    direction TEXT NOT NULL DEFAULT 'history', event_id TEXT UNIQUE, idempotency_key TEXT UNIQUE);

CREATE TABLE IF NOT EXISTS gmail_threads (thread_id TEXT PRIMARY KEY, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS gmail_messages (
    message_id TEXT PRIMARY KEY, thread_id TEXT NOT NULL, from_addr TEXT NOT NULL, to_addrs TEXT NOT NULL,
    cc_addrs TEXT NOT NULL DEFAULT '[]', subject TEXT NOT NULL, sent_at TEXT NOT NULL, body TEXT NOT NULL,
    direction TEXT NOT NULL DEFAULT 'history', event_id TEXT UNIQUE, idempotency_key TEXT UNIQUE);

CREATE TABLE IF NOT EXISTS agent_followups (
    followup_id TEXT PRIMARY KEY, task_id TEXT NOT NULL, recipient_id TEXT NOT NULL, channel TEXT NOT NULL,
    purpose TEXT NOT NULL, question_key TEXT NOT NULL, subject TEXT, draft TEXT NOT NULL, reason TEXT NOT NULL,
    evidence TEXT NOT NULL DEFAULT '[]', approval_id TEXT, status TEXT NOT NULL, attempt INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL, sent_at TEXT, external_message_id TEXT, thread_id TEXT,
    reply_status TEXT NOT NULL DEFAULT 'none', last_reply_at TEXT, next_allowed_at TEXT,
    current_commitment TEXT, created_by TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS agent_approvals (
    approval_id TEXT PRIMARY KEY, action_type TEXT NOT NULL, payload TEXT NOT NULL, payload_hash TEXT NOT NULL,
    target TEXT NOT NULL, reason TEXT NOT NULL, evidence TEXT NOT NULL DEFAULT '[]', requested_at TEXT NOT NULL,
    requested_by TEXT NOT NULL, status TEXT NOT NULL, decided_by TEXT, decided_at TEXT, decision_note TEXT,
    executed_at TEXT, execution_result TEXT);

CREATE TABLE IF NOT EXISTS agent_proposals (
    proposal_id TEXT PRIMARY KEY, task_id TEXT NOT NULL, changes TEXT NOT NULL, reason TEXT NOT NULL,
    evidence TEXT NOT NULL DEFAULT '[]', source_message_id TEXT, approval_id TEXT, status TEXT NOT NULL,
    dedup_key TEXT UNIQUE NOT NULL, created_at TEXT NOT NULL, created_by TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS agent_processed_events (
    event_id TEXT PRIMARY KEY, message_id TEXT NOT NULL, processed_at TEXT NOT NULL, result TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS agent_commitments (
    commitment_id TEXT PRIMARY KEY, task_id TEXT NOT NULL, member_id TEXT NOT NULL, kind TEXT NOT NULL,
    commitment_date TEXT, text TEXT NOT NULL, source_message_id TEXT NOT NULL, confidence TEXT NOT NULL,
    created_at TEXT NOT NULL, superseded INTEGER NOT NULL DEFAULT 0);

CREATE TABLE IF NOT EXISTS agent_blockers (
    blocker_id TEXT PRIMARY KEY, task_id TEXT NOT NULL, description TEXT NOT NULL, source_message_id TEXT,
    reported_at TEXT NOT NULL, resolved_at TEXT);

CREATE TABLE IF NOT EXISTS agent_decisions (
    decision_id TEXT PRIMARY KEY, task_id TEXT, question TEXT NOT NULL, owner_id TEXT NOT NULL,
    reason TEXT NOT NULL, evidence TEXT NOT NULL DEFAULT '[]', status TEXT NOT NULL DEFAULT 'open',
    dedup_key TEXT UNIQUE NOT NULL, created_at TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS agent_investigations (
    investigation_id TEXT PRIMARY KEY, trace_id TEXT NOT NULL, scope TEXT NOT NULL, mode TEXT NOT NULL,
    result TEXT NOT NULL, created_at TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS agent_health_history (
    snapshot_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, created_at TEXT NOT NULL,
    classification TEXT NOT NULL, metrics TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS agent_task_snapshots (
    snapshot_id TEXT PRIMARY KEY, task_id TEXT NOT NULL, captured_at TEXT NOT NULL, data TEXT NOT NULL, reason TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS agent_exemptions (
    target_type TEXT NOT NULL, target_id TEXT NOT NULL, until TEXT NOT NULL, reason TEXT NOT NULL,
    PRIMARY KEY (target_type, target_id));
"""


class NotInitializedError(RuntimeError):
    pass


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8].upper()}"


def _j(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


class Store:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(db_path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)

    def close(self) -> None:
        self.conn.close()

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        try:
            yield self.conn
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    # ------------------------------------------------------------------ meta / clock
    def get_meta(self, key: str, default: str | None = None) -> str | None:
        row = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    def set_meta(self, key: str, value: str) -> None:
        with self.tx() as c:
            c.execute("INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                      (key, value))

    @property
    def initialized(self) -> bool:
        return self.get_meta("initialized_at") is not None

    def require_initialized(self) -> None:
        if not self.initialized:
            raise NotInitializedError("The mock project is not initialized. Run: python run.py init")

    def now(self) -> datetime:
        self.require_initialized()
        return parse_dt(self.get_meta("sim_now"))  # type: ignore[arg-type]

    def set_now(self, dt: datetime) -> None:
        self.set_meta("sim_now", dt.isoformat())

    def advance_clock(self, hours: float) -> datetime:
        new = self.now() + timedelta(hours=hours)
        self.set_now(new)
        return new

    # ------------------------------------------------------------------ load / reset
    def reset(self) -> None:
        with self.tx() as c:
            tables = [r["name"] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")]
            for t in tables:
                c.execute(f"DELETE FROM {t}")

    def load_dataset(self, data_dir: Path, scenario: str) -> dict[str, Any]:
        """Wipe everything and load the fictional dataset + scenario overlays."""
        scen_path = data_dir / "scenarios" / scenario / "scenario.json"
        if not scen_path.exists():
            available = sorted(p.name for p in (data_dir / "scenarios").iterdir() if p.is_dir())
            raise FileNotFoundError(f"Unknown scenario {scenario!r}. Available: {', '.join(available)}")
        scen = json.loads(scen_path.read_text(encoding="utf-8"))

        def load(rel: str) -> dict[str, Any]:
            path = data_dir / rel
            if not path.exists():
                raise FileNotFoundError(f"Missing mock data file: {path}")
            return json.loads(path.read_text(encoding="utf-8"))

        people = load("people/team-members.json")
        projects = load("monday/projects.json")
        tasks = load("monday/tasks.json")
        deps = load("monday/dependencies.json")
        updates = load("monday/task-updates.json")
        channels = load("slack/channels.json")
        messages = load("slack/messages.json")
        load("slack/threads.json")  # validated for presence; thread ids live on messages
        email = load("gmail/email-threads.json")

        self.reset()
        now = scen["clock"]
        overlays = scen.get("overlays", {})
        with self.tx() as c:
            for m in people["members"]:
                c.execute("INSERT INTO people_members VALUES(?,?)", (m["member_id"], _j(m)))
            for x in people.get("external_contacts", []):
                c.execute("INSERT INTO people_contacts VALUES(?,?)", (x["contact_id"], _j(x)))
            for p in projects["projects"]:
                c.execute("INSERT INTO monday_projects VALUES(?,?)", (p["project_id"], _j(p)))
            for t in tasks["tasks"]:
                t = {**t, **overlays.get("task_overrides", {}).get(t["task_id"], {})}
                c.execute("INSERT INTO monday_tasks(task_id,project_id,data,version,updated_at) VALUES(?,?,?,?,?)",
                          (t["task_id"], t["project_id"], _j(t), 1, now))
            for d in deps["dependencies"]:
                c.execute("INSERT INTO monday_dependencies VALUES(?,?)", (d["task_id"], d["depends_on"]))
            for u in updates["updates"]:
                c.execute("INSERT INTO monday_updates VALUES(?,?,?,?,?,?)",
                          (u["update_id"], u["task_id"], u["author_id"], u["created_at"], u["body"], "board"))
            c.execute("INSERT INTO meta VALUES('slack_bot_user_id', ?)", (channels["bot_user_id"],))
            for ch in channels["channels"]:
                c.execute("INSERT INTO slack_channels VALUES(?,?)", (ch["channel_id"], _j(ch)))
            c.execute("INSERT INTO meta VALUES('slack_external_users', ?)", (_j(channels.get("external_users", [])),))
            for msg in messages["messages"] + overlays.get("extra_slack_messages", []):
                c.execute("INSERT INTO slack_messages(message_id,channel_id,thread_id,author_slack_id,ts,text,task_refs,direction)"
                          " VALUES(?,?,?,?,?,?,?,?)",
                          (msg["message_id"], msg["channel_id"], msg.get("thread_id"), msg["author_slack_id"], msg["ts"],
                           msg["text"], _j(msg.get("task_refs", [])), "history"))
            c.execute("INSERT INTO meta VALUES('gmail_mailbox', ?)", (email["mailbox"],))
            for th in email["threads"]:
                meta = {k: v for k, v in th.items() if k != "messages"}
                c.execute("INSERT INTO gmail_threads VALUES(?,?)", (th["thread_id"], _j(meta)))
                for em in th["messages"]:
                    c.execute("INSERT INTO gmail_messages(message_id,thread_id,from_addr,to_addrs,cc_addrs,subject,sent_at,body,direction)"
                              " VALUES(?,?,?,?,?,?,?,?,?)",
                              (em["message_id"], th["thread_id"], em["from"], _j(em["to"]), _j(em.get("cc", [])),
                               th["subject"], em["sent_at"], em["body"], "history"))
            c.execute("INSERT INTO meta VALUES('scenario', ?)", (scenario,))
            c.execute("INSERT INTO meta VALUES('sim_now', ?)", (now,))
            c.execute("INSERT INTO meta VALUES('scenario_clock', ?)", (now,))
            c.execute("INSERT INTO meta VALUES('initialized_at', ?)", (now,))
        for t in self.list_tasks():
            self.snapshot_task(t, "initial load")
        return scen

    def scenario(self) -> dict[str, Any]:
        return {"name": self.get_meta("scenario"), "clock": self.get_meta("scenario_clock")}

    # ------------------------------------------------------------------ people
    def list_members(self, include_off_project: bool = True) -> list[dict[str, Any]]:
        rows = [json.loads(r["data"]) for r in self.conn.execute("SELECT data FROM people_members ORDER BY member_id")]
        return rows if include_off_project else [m for m in rows if m.get("on_project")]

    def get_member(self, member_id: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT data FROM people_members WHERE member_id=?", (member_id,)).fetchone()
        return json.loads(row["data"]) if row else None

    def member_by_slack_id(self, slack_id: str) -> dict[str, Any] | None:
        return next((m for m in self.list_members() if m.get("slack_user_id") == slack_id), None)

    def member_by_email(self, email: str) -> dict[str, Any] | None:
        return next((m for m in self.list_members() if m.get("email", "").lower() == email.lower()), None)

    def list_contacts(self) -> list[dict[str, Any]]:
        return [json.loads(r["data"]) for r in self.conn.execute("SELECT data FROM people_contacts ORDER BY contact_id")]

    def get_contact(self, contact_id: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT data FROM people_contacts WHERE contact_id=?", (contact_id,)).fetchone()
        return json.loads(row["data"]) if row else None

    # ------------------------------------------------------------------ monday (simulated)
    def list_projects(self) -> list[dict[str, Any]]:
        return [json.loads(r["data"]) for r in self.conn.execute("SELECT data FROM monday_projects")]

    def get_project(self, project_id: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT data FROM monday_projects WHERE project_id=?", (project_id,)).fetchone()
        return json.loads(row["data"]) if row else None

    def default_project_id(self) -> str:
        projects = self.list_projects()
        if not projects:
            raise NotInitializedError("No projects loaded. Run: python run.py init")
        return projects[0]["project_id"]

    def list_tasks(self, project_id: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT data, version FROM monday_tasks"
        args: tuple[Any, ...] = ()
        if project_id:
            sql += " WHERE project_id=?"
            args = (project_id,)
        out = []
        for r in self.conn.execute(sql + " ORDER BY task_id", args):
            t = json.loads(r["data"])
            t["version"] = r["version"]
            out.append(t)
        return out

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT data, version FROM monday_tasks WHERE task_id=?", (task_id,)).fetchone()
        if not row:
            return None
        t = json.loads(row["data"])
        t["version"] = row["version"]
        return t

    def write_task(self, task: dict[str, Any], *, expected_version: int) -> int:
        """Optimistic-concurrency write. Only called by the approved-change executor."""
        data = {k: v for k, v in task.items() if k != "version"}
        with self.tx() as c:
            cur = c.execute("UPDATE monday_tasks SET data=?, version=version+1, updated_at=? WHERE task_id=? AND version=?",
                            (_j(data), self.get_meta("sim_now"), task["task_id"], expected_version))
            if cur.rowcount != 1:
                raise RuntimeError(f"Task {task['task_id']} changed since the proposal was made (version conflict).")
        return expected_version + 1

    def list_dependencies(self) -> list[tuple[str, str]]:
        return [(r["task_id"], r["depends_on"]) for r in
                self.conn.execute("SELECT task_id, depends_on FROM monday_dependencies ORDER BY task_id, depends_on")]

    def add_dependency(self, task_id: str, depends_on: str) -> None:
        with self.tx() as c:
            c.execute("INSERT OR IGNORE INTO monday_dependencies VALUES(?,?)", (task_id, depends_on))

    def list_updates(self, task_id: str | None = None) -> list[dict[str, Any]]:
        if task_id:
            rows = self.conn.execute("SELECT * FROM monday_updates WHERE task_id=? ORDER BY created_at", (task_id,))
        else:
            rows = self.conn.execute("SELECT * FROM monday_updates ORDER BY created_at")
        return [dict(r) for r in rows]

    def add_update(self, task_id: str, author_id: str, body: str, source: str) -> str:
        uid = new_id("UPD")
        with self.tx() as c:
            c.execute("INSERT INTO monday_updates VALUES(?,?,?,?,?,?)",
                      (uid, task_id, author_id, self.get_meta("sim_now"), body, source))
        return uid

    def log_change(self, task_id: str, approval_id: str, before: dict[str, Any], after: dict[str, Any]) -> None:
        with self.tx() as c:
            c.execute("INSERT INTO monday_change_log VALUES(?,?,?,?,?,?)",
                      (new_id("CHG"), task_id, approval_id, _j(before), _j(after), self.get_meta("sim_now")))

    def list_changes(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self.conn.execute("SELECT * FROM monday_change_log ORDER BY applied_at")]

    def snapshot_task(self, task: dict[str, Any], reason: str) -> None:
        with self.tx() as c:
            c.execute("INSERT INTO agent_task_snapshots VALUES(?,?,?,?,?)",
                      (new_id("SNAP"), task["task_id"], self.get_meta("sim_now") or "", _j(task), reason))

    # ------------------------------------------------------------------ slack (simulated)
    def list_channels(self) -> list[dict[str, Any]]:
        return [json.loads(r["data"]) for r in self.conn.execute("SELECT data FROM slack_channels")]

    def get_channel(self, channel_id: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT data FROM slack_channels WHERE channel_id=?", (channel_id,)).fetchone()
        return json.loads(row["data"]) if row else None

    def _slack_row(self, r: sqlite3.Row) -> dict[str, Any]:
        d = dict(r)
        d["task_refs"] = json.loads(d["task_refs"])
        return d

    def list_slack_messages(self, channel_id: str | None = None) -> list[dict[str, Any]]:
        if channel_id:
            rows = self.conn.execute("SELECT * FROM slack_messages WHERE channel_id=? ORDER BY ts", (channel_id,))
        else:
            rows = self.conn.execute("SELECT * FROM slack_messages ORDER BY ts")
        return [self._slack_row(r) for r in rows]

    def get_slack_message(self, message_id: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM slack_messages WHERE message_id=?", (message_id,)).fetchone()
        return self._slack_row(row) if row else None

    def slack_message_by_key(self, *, event_id: str | None = None, idempotency_key: str | None = None) -> dict[str, Any] | None:
        col, val = ("event_id", event_id) if event_id else ("idempotency_key", idempotency_key)
        row = self.conn.execute(f"SELECT * FROM slack_messages WHERE {col}=?", (val,)).fetchone()
        return self._slack_row(row) if row else None

    def insert_slack_message(self, msg: dict[str, Any]) -> None:
        with self.tx() as c:
            c.execute("INSERT INTO slack_messages(message_id,channel_id,thread_id,author_slack_id,ts,text,task_refs,direction,"
                      "event_id,idempotency_key) VALUES(?,?,?,?,?,?,?,?,?,?)",
                      (msg["message_id"], msg["channel_id"], msg.get("thread_id"), msg["author_slack_id"], msg["ts"],
                       msg["text"], _j(msg.get("task_refs", [])), msg["direction"], msg.get("event_id"),
                       msg.get("idempotency_key")))

    # ------------------------------------------------------------------ gmail (simulated)
    def list_email_threads(self) -> list[dict[str, Any]]:
        return [json.loads(r["data"]) for r in self.conn.execute("SELECT data FROM gmail_threads ORDER BY thread_id")]

    def get_email_thread_meta(self, thread_id: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT data FROM gmail_threads WHERE thread_id=?", (thread_id,)).fetchone()
        return json.loads(row["data"]) if row else None

    def upsert_email_thread(self, meta: dict[str, Any]) -> None:
        with self.tx() as c:
            c.execute("INSERT INTO gmail_threads VALUES(?,?) ON CONFLICT(thread_id) DO UPDATE SET data=excluded.data",
                      (meta["thread_id"], _j(meta)))

    def _email_row(self, r: sqlite3.Row) -> dict[str, Any]:
        d = dict(r)
        d["to_addrs"] = json.loads(d["to_addrs"])
        d["cc_addrs"] = json.loads(d["cc_addrs"])
        return d

    def list_email_messages(self, thread_id: str | None = None) -> list[dict[str, Any]]:
        if thread_id:
            rows = self.conn.execute("SELECT * FROM gmail_messages WHERE thread_id=? ORDER BY sent_at", (thread_id,))
        else:
            rows = self.conn.execute("SELECT * FROM gmail_messages ORDER BY sent_at")
        return [self._email_row(r) for r in rows]

    def get_email_message(self, message_id: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM gmail_messages WHERE message_id=?", (message_id,)).fetchone()
        return self._email_row(row) if row else None

    def email_by_key(self, idempotency_key: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM gmail_messages WHERE idempotency_key=?", (idempotency_key,)).fetchone()
        return self._email_row(row) if row else None

    def insert_email_message(self, msg: dict[str, Any]) -> None:
        with self.tx() as c:
            c.execute("INSERT INTO gmail_messages(message_id,thread_id,from_addr,to_addrs,cc_addrs,subject,sent_at,body,direction,"
                      "event_id,idempotency_key) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                      (msg["message_id"], msg["thread_id"], msg["from_addr"], _j(msg["to_addrs"]), _j(msg.get("cc_addrs", [])),
                       msg["subject"], msg["sent_at"], msg["body"], msg["direction"], msg.get("event_id"),
                       msg.get("idempotency_key")))

    # ------------------------------------------------------------------ agent: follow-ups
    def _fu(self, r: sqlite3.Row) -> dict[str, Any]:
        d = dict(r)
        d["evidence"] = json.loads(d["evidence"])
        return d

    def insert_followup(self, fu: dict[str, Any]) -> None:
        cols = ["followup_id", "task_id", "recipient_id", "channel", "purpose", "question_key", "subject", "draft", "reason",
                "evidence", "approval_id", "status", "attempt", "created_at", "sent_at", "external_message_id", "thread_id",
                "reply_status", "last_reply_at", "next_allowed_at", "current_commitment", "created_by"]
        row = {**{c: None for c in cols}, "reply_status": "none", "attempt": 1, **fu}
        row["evidence"] = _j(row.get("evidence") or [])
        with self.tx() as c:
            c.execute(f"INSERT INTO agent_followups({','.join(cols)}) VALUES({','.join('?' * len(cols))})",
                      tuple(row[k] for k in cols))

    def update_followup(self, followup_id: str, **fields: Any) -> None:
        if not fields:
            return
        sets = ", ".join(f"{k}=?" for k in fields)
        with self.tx() as c:
            c.execute(f"UPDATE agent_followups SET {sets} WHERE followup_id=?", (*fields.values(), followup_id))

    def get_followup(self, followup_id: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM agent_followups WHERE followup_id=?", (followup_id,)).fetchone()
        return self._fu(row) if row else None

    def list_followups(self, *, task_id: str | None = None, recipient_id: str | None = None,
                       statuses: set[str] | None = None) -> list[dict[str, Any]]:
        rows = [self._fu(r) for r in self.conn.execute("SELECT * FROM agent_followups ORDER BY created_at, followup_id")]
        return [r for r in rows if (task_id is None or r["task_id"] == task_id)
                and (recipient_id is None or r["recipient_id"] == recipient_id)
                and (statuses is None or r["status"] in statuses)]

    def followup_by_thread(self, thread_id: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM agent_followups WHERE thread_id=? ORDER BY sent_at DESC", (thread_id,)).fetchone()
        return self._fu(row) if row else None

    # ------------------------------------------------------------------ agent: approvals
    def _ap(self, r: sqlite3.Row) -> dict[str, Any]:
        d = dict(r)
        d["payload"] = json.loads(d["payload"])
        d["evidence"] = json.loads(d["evidence"])
        d["execution_result"] = json.loads(d["execution_result"]) if d["execution_result"] else None
        return d

    def insert_approval(self, ap: dict[str, Any]) -> None:
        with self.tx() as c:
            c.execute("INSERT INTO agent_approvals(approval_id,action_type,payload,payload_hash,target,reason,evidence,"
                      "requested_at,requested_by,status) VALUES(?,?,?,?,?,?,?,?,?,?)",
                      (ap["approval_id"], ap["action_type"], _j(ap["payload"]), ap["payload_hash"], ap["target"],
                       ap["reason"], _j(ap.get("evidence", [])), ap["requested_at"], ap["requested_by"], "pending"))

    def update_approval(self, approval_id: str, **fields: Any) -> None:
        if "execution_result" in fields and not isinstance(fields["execution_result"], str):
            fields["execution_result"] = _j(fields["execution_result"])
        sets = ", ".join(f"{k}=?" for k in fields)
        with self.tx() as c:
            c.execute(f"UPDATE agent_approvals SET {sets} WHERE approval_id=?", (*fields.values(), approval_id))

    def get_approval(self, approval_id: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM agent_approvals WHERE approval_id=?", (approval_id,)).fetchone()
        return self._ap(row) if row else None

    def list_approvals(self, statuses: set[str] | None = None) -> list[dict[str, Any]]:
        rows = [self._ap(r) for r in self.conn.execute("SELECT * FROM agent_approvals ORDER BY requested_at, approval_id")]
        return [r for r in rows if statuses is None or r["status"] in statuses]

    # ------------------------------------------------------------------ agent: proposals
    def _pr(self, r: sqlite3.Row) -> dict[str, Any]:
        d = dict(r)
        d["changes"] = json.loads(d["changes"])
        d["evidence"] = json.loads(d["evidence"])
        return d

    def proposal_by_dedup(self, dedup_key: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM agent_proposals WHERE dedup_key=?", (dedup_key,)).fetchone()
        return self._pr(row) if row else None

    def insert_proposal(self, pr: dict[str, Any]) -> None:
        with self.tx() as c:
            c.execute("INSERT INTO agent_proposals VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                      (pr["proposal_id"], pr["task_id"], _j(pr["changes"]), pr["reason"], _j(pr.get("evidence", [])),
                       pr.get("source_message_id"), pr.get("approval_id"), pr["status"], pr["dedup_key"],
                       pr["created_at"], pr["created_by"]))

    def update_proposal(self, proposal_id: str, **fields: Any) -> None:
        sets = ", ".join(f"{k}=?" for k in fields)
        with self.tx() as c:
            c.execute(f"UPDATE agent_proposals SET {sets} WHERE proposal_id=?", (*fields.values(), proposal_id))

    def list_proposals(self, task_id: str | None = None) -> list[dict[str, Any]]:
        rows = [self._pr(r) for r in self.conn.execute("SELECT * FROM agent_proposals ORDER BY created_at, proposal_id")]
        return [r for r in rows if task_id is None or r["task_id"] == task_id]

    def proposal_by_approval(self, approval_id: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM agent_proposals WHERE approval_id=?", (approval_id,)).fetchone()
        return self._pr(row) if row else None

    # ------------------------------------------------------------------ agent: events, commitments, blockers, decisions
    def processed_event(self, event_id: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM agent_processed_events WHERE event_id=?", (event_id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        d["result"] = json.loads(d["result"])
        return d

    def record_processed_event(self, event_id: str, message_id: str, result: dict[str, Any]) -> None:
        with self.tx() as c:
            c.execute("INSERT INTO agent_processed_events VALUES(?,?,?,?)",
                      (event_id, message_id, self.get_meta("sim_now"), _j(result)))

    def list_processed_events(self) -> list[dict[str, Any]]:
        out = []
        for r in self.conn.execute("SELECT * FROM agent_processed_events ORDER BY processed_at"):
            d = dict(r)
            d["result"] = json.loads(d["result"])
            out.append(d)
        return out

    def insert_commitment(self, cm: dict[str, Any]) -> str:
        cid = new_id("CMT")
        with self.tx() as c:
            c.execute("UPDATE agent_commitments SET superseded=1 WHERE task_id=? AND member_id=?", (cm["task_id"], cm["member_id"]))
            c.execute("INSERT INTO agent_commitments VALUES(?,?,?,?,?,?,?,?,?,0)",
                      (cid, cm["task_id"], cm["member_id"], cm["kind"], cm.get("commitment_date"), cm["text"],
                       cm["source_message_id"], cm["confidence"], self.get_meta("sim_now")))
        return cid

    def list_commitments(self, task_id: str | None = None, *, current_only: bool = False) -> list[dict[str, Any]]:
        rows = [dict(r) for r in self.conn.execute("SELECT * FROM agent_commitments ORDER BY created_at")]
        return [r for r in rows if (task_id is None or r["task_id"] == task_id) and (not current_only or not r["superseded"])]

    def insert_blocker(self, task_id: str, description: str, source_message_id: str | None) -> str:
        bid = new_id("BLK")
        with self.tx() as c:
            c.execute("INSERT INTO agent_blockers VALUES(?,?,?,?,?,NULL)",
                      (bid, task_id, description, source_message_id, self.get_meta("sim_now")))
        return bid

    def list_blockers(self, open_only: bool = True) -> list[dict[str, Any]]:
        rows = [dict(r) for r in self.conn.execute("SELECT * FROM agent_blockers ORDER BY reported_at")]
        return [r for r in rows if not open_only or r["resolved_at"] is None]

    def resolve_blockers(self, task_id: str) -> None:
        with self.tx() as c:
            c.execute("UPDATE agent_blockers SET resolved_at=? WHERE task_id=? AND resolved_at IS NULL",
                      (self.get_meta("sim_now"), task_id))

    def upsert_decision(self, d: dict[str, Any]) -> str:
        existing = self.conn.execute("SELECT decision_id FROM agent_decisions WHERE dedup_key=?", (d["dedup_key"],)).fetchone()
        if existing:
            return existing["decision_id"]
        did = new_id("DEC")
        with self.tx() as c:
            c.execute("INSERT INTO agent_decisions VALUES(?,?,?,?,?,?,?,?,?)",
                      (did, d.get("task_id"), d["question"], d["owner_id"], d["reason"], _j(d.get("evidence", [])),
                       "open", d["dedup_key"], self.get_meta("sim_now")))
        return did

    def list_decisions(self, open_only: bool = True) -> list[dict[str, Any]]:
        rows = []
        for r in self.conn.execute("SELECT * FROM agent_decisions ORDER BY created_at"):
            d = dict(r)
            d["evidence"] = json.loads(d["evidence"])
            rows.append(d)
        return [r for r in rows if not open_only or r["status"] == "open"]

    def close_decision(self, decision_id: str, status: str = "resolved") -> None:
        with self.tx() as c:
            c.execute("UPDATE agent_decisions SET status=? WHERE decision_id=?", (status, decision_id))

    # ------------------------------------------------------------------ agent: investigations, health, exemptions
    def insert_investigation(self, trace_id: str, scope: str, mode: str, result: dict[str, Any]) -> str:
        iid = new_id("INV")
        with self.tx() as c:
            c.execute("INSERT INTO agent_investigations VALUES(?,?,?,?,?,?)",
                      (iid, trace_id, scope, mode, _j(result), self.get_meta("sim_now")))
        return iid

    def list_investigations(self) -> list[dict[str, Any]]:
        out = []
        for r in self.conn.execute("SELECT * FROM agent_investigations ORDER BY created_at"):
            d = dict(r)
            d["result"] = json.loads(d["result"])
            out.append(d)
        return out

    def insert_health(self, project_id: str, classification: str, metrics: dict[str, Any]) -> None:
        with self.tx() as c:
            c.execute("INSERT INTO agent_health_history VALUES(?,?,?,?,?)",
                      (new_id("HLT"), project_id, self.get_meta("sim_now"), classification, _j(metrics)))

    def list_health(self) -> list[dict[str, Any]]:
        out = []
        for r in self.conn.execute("SELECT * FROM agent_health_history ORDER BY created_at, rowid"):
            d = dict(r)
            d["metrics"] = json.loads(d["metrics"])
            out.append(d)
        return out

    def set_exemption(self, target_type: str, target_id: str, until: str, reason: str) -> None:
        with self.tx() as c:
            c.execute("INSERT INTO agent_exemptions VALUES(?,?,?,?) ON CONFLICT(target_type,target_id) "
                      "DO UPDATE SET until=excluded.until, reason=excluded.reason", (target_type, target_id, until, reason))

    def list_exemptions(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self.conn.execute("SELECT * FROM agent_exemptions")]


def open_store(settings: Any) -> Store:
    return Store(settings.db_path)
