"""SQLite persistence for the control plane.

One file, one connection, guarded by a lock. Tasks survive a restart, which is
what makes checkpoints and recovery possible.

SQLite is deliberate for the prototype: no server to run, no extra moving
parts. The store is the only place that knows about SQL, so swapping in
PostgreSQL later means rewriting this module and nothing else.
"""

import sqlite3
import threading
import time
from pathlib import Path

from assistant.control.models import (
    ActivityEvent,
    Approval,
    ApprovalStatus,
    Decision,
    Device,
    DeviceStatus,
    EventType,
    Helper,
    HelperStatus,
    Permission,
    PermissionStatus,
    PolicyRule,
    RiskLevel,
    StepStatus,
    Task,
    TaskStatus,
    TaskStep,
    dumps,
    loads,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS devices (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,
    platform TEXT,
    status TEXT NOT NULL,
    last_seen REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS helpers (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    framework TEXT NOT NULL,
    device_id TEXT,
    status TEXT NOT NULL,
    capabilities TEXT NOT NULL,
    current_task_id TEXT,
    last_active REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    goal TEXT NOT NULL,
    status TEXT NOT NULL,
    helper_id TEXT,
    device_id TEXT,
    summary TEXT,
    checkpoint TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS task_steps (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    label TEXT NOT NULL,
    status TEXT NOT NULL,
    detail TEXT,
    FOREIGN KEY (task_id) REFERENCES tasks (id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS permissions (
    id TEXT PRIMARY KEY,
    task_id TEXT,
    resource TEXT NOT NULL,
    actions TEXT NOT NULL,
    device_id TEXT,
    status TEXT NOT NULL,
    granted_at REAL NOT NULL,
    expires_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS approvals (
    id TEXT PRIMARY KEY,
    task_id TEXT,
    action TEXT NOT NULL,
    question TEXT NOT NULL,
    reason TEXT,
    impact TEXT,
    status TEXT NOT NULL,
    created_at REAL NOT NULL,
    resolved_at REAL
);

CREATE TABLE IF NOT EXISTS activity_events (
    id TEXT PRIMARY KEY,
    task_id TEXT,
    type TEXT NOT NULL,
    message TEXT NOT NULL,
    actor TEXT,
    device_id TEXT,
    timestamp REAL NOT NULL,
    metadata TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_steps_task ON task_steps (task_id, position);
CREATE INDEX IF NOT EXISTS idx_events_time ON activity_events (timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_events_task ON activity_events (task_id);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks (status, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_approvals_status ON approvals (status, created_at DESC);
"""

# Ordered, named migrations. Every schema change goes here rather than into
# SCHEMA, so an existing data/control.db is upgraded in place instead of
# silently missing columns. Names are permanent; never renumber or reorder.
MIGRATIONS = [
    ("0001_baseline", SCHEMA),
    ("0002_device_pairing", [
        ("devices", "token_hash", "ALTER TABLE devices ADD COLUMN token_hash TEXT"),
        ("devices", "paired_at", "ALTER TABLE devices ADD COLUMN paired_at REAL"),
    ]),
    ("0003_policy_rules", """
        CREATE TABLE IF NOT EXISTS policy_rules (
            id TEXT PRIMARY KEY,
            capability TEXT NOT NULL,
            decision TEXT NOT NULL,
            agent_id TEXT,
            task_id TEXT,
            resource TEXT,
            reason TEXT,
            created_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_rules_capability ON policy_rules (capability);
    """),
    ("0004_approval_capabilities", [
        ("approvals", "capability", "ALTER TABLE approvals ADD COLUMN capability TEXT"),
        ("approvals", "resource", "ALTER TABLE approvals ADD COLUMN resource TEXT"),
        ("approvals", "risk", "ALTER TABLE approvals ADD COLUMN risk TEXT"),
        ("approvals", "agent_id", "ALTER TABLE approvals ADD COLUMN agent_id TEXT"),
        ("approvals", "seconds", "ALTER TABLE approvals ADD COLUMN seconds INTEGER"),
    ]),
    ("0005_agent_health", [
        ("helpers", "version", "ALTER TABLE helpers ADD COLUMN version TEXT"),
        ("helpers", "endpoint", "ALTER TABLE helpers ADD COLUMN endpoint TEXT"),
        ("helpers", "metadata", "ALTER TABLE helpers ADD COLUMN metadata TEXT"),
        ("helpers", "enabled", "ALTER TABLE helpers ADD COLUMN enabled INTEGER DEFAULT 1"),
        ("helpers", "last_heartbeat", "ALTER TABLE helpers ADD COLUMN last_heartbeat REAL"),
        ("helpers", "success_count", "ALTER TABLE helpers ADD COLUMN success_count INTEGER DEFAULT 0"),
        ("helpers", "error_count", "ALTER TABLE helpers ADD COLUMN error_count INTEGER DEFAULT 0"),
        ("helpers", "latencies", "ALTER TABLE helpers ADD COLUMN latencies TEXT"),
    ]),
    ("0006_device_capabilities", [
        ("devices", "capabilities", "ALTER TABLE devices ADD COLUMN capabilities TEXT"),
    ]),
    ("0007_permission_agent", [
        ("permissions", "agent_id", "ALTER TABLE permissions ADD COLUMN agent_id TEXT"),
    ]),
    ("0008_step_graph", [
        ("task_steps", "depends_on", "ALTER TABLE task_steps ADD COLUMN depends_on TEXT"),
        ("task_steps", "agent_id", "ALTER TABLE task_steps ADD COLUMN agent_id TEXT"),
        ("task_steps", "capability", "ALTER TABLE task_steps ADD COLUMN capability TEXT"),
    ]),
    ("0009_step_attempts", [
        ("task_steps", "attempts", "ALTER TABLE task_steps ADD COLUMN attempts INTEGER DEFAULT 0"),
        ("task_steps", "artifacts", "ALTER TABLE task_steps ADD COLUMN artifacts TEXT"),
    ]),
    ("0010_event_details", [
        ("activity_events", "agent_id", "ALTER TABLE activity_events ADD COLUMN agent_id TEXT"),
        ("activity_events", "capability", "ALTER TABLE activity_events ADD COLUMN capability TEXT"),
        ("activity_events", "risk", "ALTER TABLE activity_events ADD COLUMN risk TEXT"),
        ("activity_events", "approval_id", "ALTER TABLE activity_events ADD COLUMN approval_id TEXT"),
        ("activity_events", "result", "ALTER TABLE activity_events ADD COLUMN result TEXT"),
    ]),
    ("0011_secrets", """
        CREATE TABLE IF NOT EXISTS secrets (
            name TEXT PRIMARY KEY,
            ciphertext TEXT NOT NULL,
            description TEXT,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );
    """),
    ("0012_device_token_expiry", [
        ("devices", "token_expires_at", "ALTER TABLE devices ADD COLUMN token_expires_at REAL DEFAULT 0.0"),
    ]),
    ("0013_persistent_notifications", """
        CREATE TABLE IF NOT EXISTS notifications (
            id TEXT PRIMARY KEY,
            type TEXT NOT NULL,
            urgency TEXT NOT NULL,
            message TEXT NOT NULL,
            task_id TEXT,
            agent_id TEXT,
            capability TEXT,
            risk TEXT,
            approval_id TEXT,
            needs_answer INTEGER NOT NULL DEFAULT 0,
            read INTEGER NOT NULL DEFAULT 0,
            timestamp REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_notifications_time ON notifications (timestamp DESC);
    """),
    ("0014_secret_scopes", [
        ("secrets", "allowed_capabilities", "ALTER TABLE secrets ADD COLUMN allowed_capabilities TEXT DEFAULT ''"),
    ]),
]

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "control.db"


class ControlStore:
    """Thread-safe SQLite storage for control-plane records."""

    def __init__(self, db_path=None):
        self.db_path = Path(db_path or DEFAULT_DB_PATH)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        # check_same_thread=False because worker threads share this connection;
        # every access is serialised through _lock.
        self._connection = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.execute("PRAGMA journal_mode = WAL")
        self._lock = threading.RLock()

        with self._lock:
            self._migrate()

    def close(self):
        with self._lock:
            if self._connection:
                try:
                    self._connection.close()
                except Exception:
                    pass
                self._connection = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    # -- migrations ---------------------------------------------------------

    def _migrate(self):
        """Bring the database up to date, one named migration at a time.

        A migration is either a SQL script or a list of column additions,
        each guarded by the column it adds so re-running is harmless.
        """
        self._connection.execute(
            "CREATE TABLE IF NOT EXISTS schema_version ("
            "  name TEXT PRIMARY KEY,"
            "  applied_at REAL NOT NULL"
            ")")

        applied = {row["name"] for row in
                   self._connection.execute("SELECT name FROM schema_version")}

        for name, migration in MIGRATIONS:
            if name in applied:
                continue

            if isinstance(migration, str):
                self._connection.executescript(migration)
            else:
                for table, column, statement in migration:
                    if not self._has_column(table, column):
                        self._connection.execute(statement)

            self._connection.execute(
                "INSERT INTO schema_version (name, applied_at) VALUES (?, ?)",
                (name, time.time()))

        self._connection.commit()

    def _has_column(self, table, column):
        rows = self._connection.execute(f"PRAGMA table_info({table})").fetchall()
        return any(row["name"] == column for row in rows)

    def schema_versions(self):
        """Migrations applied to this database, oldest first."""
        return [row["name"] for row in
                self._rows("SELECT name FROM schema_version ORDER BY applied_at")]

    # -- low level ----------------------------------------------------------

    def _write(self, sql, params=()):
        with self._lock:
            cursor = self._connection.execute(sql, params)
            self._connection.commit()
            return cursor

    def _rows(self, sql, params=()):
        with self._lock:
            return self._connection.execute(sql, params).fetchall()

    def _row(self, sql, params=()):
        with self._lock:
            return self._connection.execute(sql, params).fetchone()

    # -- devices ------------------------------------------------------------

    def save_device(self, device):
        self._write(
            "INSERT INTO devices (id, name, kind, platform, status, last_seen, "
            "token_hash, paired_at, capabilities, token_expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET name=excluded.name, kind=excluded.kind, "
            "platform=excluded.platform, status=excluded.status, "
            "last_seen=excluded.last_seen, token_hash=excluded.token_hash, "
            "paired_at=excluded.paired_at, capabilities=excluded.capabilities, "
            "token_expires_at=excluded.token_expires_at",
            (device.id, device.name, device.kind, device.platform,
             device.status.value, device.last_seen, device.token_hash,
             device.paired_at, dumps(device.capabilities),
             getattr(device, "token_expires_at", 0.0)),
        )
        return device

    def get_device_by_token(self, token_hash):
        """Look a device up by the hash of its token. Never by the token itself."""
        if not token_hash:
            return None
        return _to_device(self._row("SELECT * FROM devices WHERE token_hash = ?",
                                    (token_hash,)))

    def get_device(self, device_id):
        row = self._row("SELECT * FROM devices WHERE id = ?", (device_id,))
        return _to_device(row)

    def list_devices(self):
        return [_to_device(row) for row in self._rows("SELECT * FROM devices ORDER BY name")]

    # -- helpers ------------------------------------------------------------

    def save_helper(self, helper):
        self._write(
            "INSERT INTO helpers (id, name, framework, device_id, status, capabilities, "
            "current_task_id, last_active, version, endpoint, metadata, enabled, "
            "last_heartbeat, success_count, error_count, latencies) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET name=excluded.name, framework=excluded.framework, "
            "device_id=excluded.device_id, status=excluded.status, "
            "capabilities=excluded.capabilities, current_task_id=excluded.current_task_id, "
            "last_active=excluded.last_active, version=excluded.version, "
            "endpoint=excluded.endpoint, metadata=excluded.metadata, "
            "enabled=excluded.enabled, last_heartbeat=excluded.last_heartbeat, "
            "success_count=excluded.success_count, error_count=excluded.error_count, "
            "latencies=excluded.latencies",
            (helper.id, helper.name, helper.framework, helper.device_id,
             helper.status.value, dumps(helper.capabilities),
             helper.current_task_id, helper.last_active, helper.version,
             helper.endpoint, dumps(helper.metadata), int(helper.enabled),
             helper.last_heartbeat, helper.success_count, helper.error_count,
             dumps(helper.latencies)),
        )
        return helper

    def get_helper(self, helper_id):
        return _to_helper(self._row("SELECT * FROM helpers WHERE id = ?", (helper_id,)))

    def list_helpers(self):
        return [_to_helper(row) for row in self._rows("SELECT * FROM helpers ORDER BY name")]

    # -- tasks --------------------------------------------------------------

    def save_task(self, task):
        self._write(
            "INSERT INTO tasks (id, goal, status, helper_id, device_id, summary, "
            "checkpoint, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET goal=excluded.goal, status=excluded.status, "
            "helper_id=excluded.helper_id, device_id=excluded.device_id, "
            "summary=excluded.summary, checkpoint=excluded.checkpoint, "
            "updated_at=excluded.updated_at",
            (task.id, task.goal, task.status.value, task.helper_id, task.device_id,
             task.summary, dumps(task.checkpoint), task.created_at, task.updated_at),
        )
        return task

    def get_task(self, task_id):
        return _to_task(self._row("SELECT * FROM tasks WHERE id = ?", (task_id,)))

    def list_tasks(self, limit=50, active_only=False):
        sql = "SELECT * FROM tasks"
        params = []
        if active_only:
            active = (TaskStatus.PENDING.value, TaskStatus.RUNNING.value,
                      TaskStatus.WAITING_APPROVAL.value)
            sql += f" WHERE status IN ({','.join('?' * len(active))})"
            params.extend(active)
        sql += " ORDER BY updated_at DESC LIMIT ?"
        params.append(limit)
        return [_to_task(row) for row in self._rows(sql, tuple(params))]

    # -- steps --------------------------------------------------------------

    def save_step(self, step):
        self._write(
            "INSERT INTO task_steps (id, task_id, position, label, status, detail, "
            "depends_on, agent_id, capability, attempts, artifacts) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET label=excluded.label, "
            "status=excluded.status, detail=excluded.detail, "
            "depends_on=excluded.depends_on, agent_id=excluded.agent_id, "
            "capability=excluded.capability, attempts=excluded.attempts, "
            "artifacts=excluded.artifacts",
            (step.id, step.task_id, step.position, step.label,
             step.status.value, step.detail, dumps(step.depends_on),
             step.agent_id, step.capability, step.attempts,
             dumps(step.artifacts)),
        )
        return step

    def get_step(self, step_id):
        return _to_step(self._row("SELECT * FROM task_steps WHERE id = ?", (step_id,)))

    def list_steps(self, task_id):
        return [_to_step(row) for row in self._rows(
            "SELECT * FROM task_steps WHERE task_id = ? ORDER BY position", (task_id,))]

    # -- permissions --------------------------------------------------------

    def save_permission(self, permission):
        self._write(
            "INSERT INTO permissions (id, task_id, resource, actions, device_id, "
            "status, granted_at, expires_at, agent_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET status=excluded.status, "
            "expires_at=excluded.expires_at",
            (permission.id, permission.task_id, permission.resource,
             dumps(permission.actions), permission.device_id,
             permission.status.value, permission.granted_at, permission.expires_at,
             permission.agent_id),
        )
        return permission

    def get_permission(self, permission_id):
        return _to_permission(
            self._row("SELECT * FROM permissions WHERE id = ?", (permission_id,)))

    def list_permissions(self, active_only=False, task_id=None, agent_id=None):
        sql = "SELECT * FROM permissions"
        clauses, params = [], []
        if active_only:
            clauses.append("status = ?")
            params.append(PermissionStatus.ACTIVE.value)
        if task_id:
            clauses.append("task_id = ?")
            params.append(task_id)
        if agent_id:
            clauses.append("agent_id = ?")
            params.append(agent_id)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY granted_at DESC"
        return [_to_permission(row) for row in self._rows(sql, tuple(params))]

    # -- approvals ----------------------------------------------------------

    def save_approval(self, approval):
        self._write(
            "INSERT INTO approvals (id, task_id, action, question, reason, impact, "
            "status, created_at, resolved_at, capability, resource, risk, agent_id, "
            "seconds) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET status=excluded.status, "
            "resolved_at=excluded.resolved_at",
            (approval.id, approval.task_id, approval.action, approval.question,
             approval.reason, approval.impact, approval.status.value,
             approval.created_at, approval.resolved_at, approval.capability,
             approval.resource, approval.risk.value, approval.agent_id,
             approval.seconds),
        )
        return approval

    # -- secrets ------------------------------------------------------------
    # Only ciphertext is stored here. The key lives outside the database, in
    # assistant/control/secrets.py.

    def save_secret(self, name, ciphertext, description="", allowed_capabilities=None, updated_at=None):
        existing = self.get_secret(name)
        created_at = existing["created_at"] if existing else (updated_at or 0.0)
        caps = (
            allowed_capabilities
            if allowed_capabilities is not None
            else (existing.get("allowed_capabilities", "") if existing else "")
        )
        self._write(
            "INSERT INTO secrets (name, ciphertext, description, allowed_capabilities, created_at, "
            "updated_at) VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(name) DO UPDATE SET ciphertext=excluded.ciphertext, "
            "description=excluded.description, "
            "allowed_capabilities=excluded.allowed_capabilities, "
            "updated_at=excluded.updated_at",
            (name, ciphertext, description, caps, created_at, updated_at or 0.0),
        )
        return self.get_secret(name)

    def get_secret(self, name):
        row = self._row("SELECT * FROM secrets WHERE name = ?", (name,))
        return dict(row) if row is not None else None

    def list_secrets(self):
        return [dict(row) for row in
                self._rows("SELECT * FROM secrets ORDER BY name")]

    def delete_secret(self, name):
        secret = self.get_secret(name)
        if secret is not None:
            self._write("DELETE FROM secrets WHERE name = ?", (name,))
        return secret

    # -- notifications ------------------------------------------------------

    def save_notification(self, data):
        self._write(
            "INSERT INTO notifications (id, type, urgency, message, task_id, "
            "agent_id, capability, risk, approval_id, needs_answer, read, timestamp) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET read=excluded.read",
            (data["id"], data["type"], data["urgency"], data["message"],
             data.get("task_id", "") or "", data.get("agent_id", "") or "",
             data.get("capability", "") or "", data.get("risk", "") or "",
             data.get("approval_id", "") or "", 1 if data.get("needs_answer") else 0,
             1 if data.get("read") else 0, data.get("timestamp", 0.0)),
        )
        return data

    def list_notifications(self, limit=50, unread_only=False):
        sql = "SELECT * FROM notifications"
        params = []
        if unread_only:
            sql += " WHERE read = 0"
        sql += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)
        return [dict(row) for row in self._rows(sql, tuple(params))]

    def mark_notification_read(self, notification_id):
        self._write("UPDATE notifications SET read = 1 WHERE id = ?", (notification_id,))
        row = self._row("SELECT * FROM notifications WHERE id = ?", (notification_id,))
        return dict(row) if row is not None else None

    # -- policy rules -------------------------------------------------------

    def save_policy_rule(self, rule):
        self._write(
            "INSERT INTO policy_rules (id, capability, decision, agent_id, task_id, "
            "resource, reason, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET capability=excluded.capability, "
            "decision=excluded.decision, agent_id=excluded.agent_id, "
            "task_id=excluded.task_id, resource=excluded.resource, "
            "reason=excluded.reason",
            (rule.id, rule.capability, rule.decision.value, rule.agent_id,
             rule.task_id, rule.resource, rule.reason, rule.created_at),
        )
        return rule

    def get_policy_rule(self, rule_id):
        return _to_policy_rule(
            self._row("SELECT * FROM policy_rules WHERE id = ?", (rule_id,)))

    def list_policy_rules(self):
        return [_to_policy_rule(row) for row in
                self._rows("SELECT * FROM policy_rules ORDER BY created_at")]

    def delete_policy_rule(self, rule_id):
        rule = self.get_policy_rule(rule_id)
        if rule is not None:
            self._write("DELETE FROM policy_rules WHERE id = ?", (rule_id,))
        return rule

    def get_approval(self, approval_id):
        return _to_approval(
            self._row("SELECT * FROM approvals WHERE id = ?", (approval_id,)))

    def list_approvals(self, pending_only=False):
        sql = "SELECT * FROM approvals"
        params = ()
        if pending_only:
            sql += " WHERE status = ?"
            params = (ApprovalStatus.PENDING.value,)
        sql += " ORDER BY created_at DESC"
        return [_to_approval(row) for row in self._rows(sql, params)]

    # -- activity -----------------------------------------------------------

    def save_event(self, event):
        self._write(
            "INSERT INTO activity_events (id, task_id, type, message, actor, "
            "device_id, timestamp, metadata, agent_id, capability, risk, "
            "approval_id, result) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (event.id, event.task_id, event.type.value, event.message, event.actor,
             event.device_id, event.timestamp, dumps(event.metadata),
             event.agent_id, event.capability, event.risk, event.approval_id,
             event.result),
        )
        return event

    def list_events(self, limit=100, task_id=None, types=None):
        sql = "SELECT * FROM activity_events"
        params = []
        clauses = []
        if task_id:
            clauses.append("task_id = ?")
            params.append(task_id)
        if types:
            placeholders = ", ".join("?" for _ in types)
            clauses.append(f"type IN ({placeholders})")
            params.extend(types)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)
        return [_to_event(row) for row in self._rows(sql, tuple(params))]


# -- row -> dataclass -------------------------------------------------------

def _to_device(row):
    if row is None:
        return None
    keys = row.keys()
    return Device(id=row["id"], name=row["name"], kind=row["kind"],
                  capabilities=loads(row["capabilities"] if "capabilities" in keys else None, []),
                  platform=row["platform"] or "",
                  status=DeviceStatus(row["status"]), last_seen=row["last_seen"],
                  token_hash=(row["token_hash"] or "") if "token_hash" in keys else "",
                  paired_at=(row["paired_at"] or 0.0) if "paired_at" in keys else 0.0,
                  token_expires_at=(row["token_expires_at"] or 0.0) if "token_expires_at" in keys else 0.0)


def _to_helper(row):
    if row is None:
        return None
    keys = row.keys()

    def value(name, fallback=None):
        return row[name] if name in keys and row[name] is not None else fallback

    return Helper(id=row["id"], name=row["name"], framework=row["framework"],
                  device_id=row["device_id"] or "",
                  status=HelperStatus(row["status"]),
                  capabilities=loads(row["capabilities"], []),
                  current_task_id=row["current_task_id"] or "",
                  last_active=row["last_active"],
                  version=value("version", ""), endpoint=value("endpoint", ""),
                  metadata=loads(value("metadata"), {}),
                  enabled=bool(value("enabled", 1)),
                  last_heartbeat=value("last_heartbeat", 0.0),
                  success_count=value("success_count", 0),
                  error_count=value("error_count", 0),
                  latencies=loads(value("latencies"), []))


def _to_task(row):
    if row is None:
        return None
    return Task(id=row["id"], goal=row["goal"], status=TaskStatus(row["status"]),
                helper_id=row["helper_id"] or "", device_id=row["device_id"] or "",
                summary=row["summary"] or "", checkpoint=loads(row["checkpoint"], {}),
                created_at=row["created_at"], updated_at=row["updated_at"])


def _to_step(row):
    if row is None:
        return None
    keys = row.keys()
    return TaskStep(id=row["id"], task_id=row["task_id"], position=row["position"],
                    label=row["label"], status=StepStatus(row["status"]),
                    detail=row["detail"] or "",
                    depends_on=loads(row["depends_on"] if "depends_on" in keys else None, []),
                    agent_id=(row["agent_id"] or "") if "agent_id" in keys else "",
                    capability=(row["capability"] or "") if "capability" in keys else "",
                    attempts=(row["attempts"] or 0) if "attempts" in keys else 0,
                    artifacts=loads(row["artifacts"] if "artifacts" in keys else None, []))


def _to_permission(row):
    if row is None:
        return None
    return Permission(id=row["id"], task_id=row["task_id"] or "",
                      resource=row["resource"], actions=loads(row["actions"], []),
                      device_id=row["device_id"] or "",
                      status=PermissionStatus(row["status"]),
                      granted_at=row["granted_at"], expires_at=row["expires_at"],
                      agent_id=(row["agent_id"] or "")
                      if "agent_id" in row.keys() else "")


def _to_approval(row):
    if row is None:
        return None
    keys = row.keys()

    def value(name, fallback=""):
        return (row[name] or fallback) if name in keys else fallback

    return Approval(id=row["id"], task_id=row["task_id"] or "", action=row["action"],
                    question=row["question"], reason=row["reason"] or "",
                    impact=row["impact"] or "", status=ApprovalStatus(row["status"]),
                    created_at=row["created_at"], resolved_at=row["resolved_at"] or 0.0,
                    capability=value("capability"), resource=value("resource"),
                    risk=RiskLevel(value("risk", RiskLevel.MEDIUM.value)),
                    agent_id=value("agent_id"), seconds=int(value("seconds", 0)))


def _to_policy_rule(row):
    if row is None:
        return None
    return PolicyRule(id=row["id"], capability=row["capability"],
                      decision=Decision(row["decision"]),
                      agent_id=row["agent_id"] or "", task_id=row["task_id"] or "",
                      resource=row["resource"] or "", reason=row["reason"] or "",
                      created_at=row["created_at"])


def _to_event(row):
    if row is None:
        return None
    keys = row.keys()

    def value(name):
        return (row[name] or "") if name in keys else ""

    return ActivityEvent(id=row["id"], task_id=row["task_id"] or "",
                         type=EventType(row["type"]), message=row["message"],
                         actor=row["actor"] or "VAVE",
                         device_id=row["device_id"] or "",
                         timestamp=row["timestamp"],
                         metadata=loads(row["metadata"], {}),
                         agent_id=value("agent_id"), capability=value("capability"),
                         risk=value("risk"), approval_id=value("approval_id"),
                         result=value("result"))
