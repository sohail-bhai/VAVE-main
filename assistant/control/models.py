"""Data model for the VAVE control plane.

These are the concepts the whole system coordinates around: the user's goals
(Task) broken into observable steps (TaskStep), the machines and AI helpers
that carry them out (Device, Helper), the access those helpers are granted
(Permission), the decisions the user must make (Approval), and the readable
record of what happened (ActivityEvent).

The types are plain dataclasses so they can cross any boundary - SQLite rows,
JSON for the desktop and mobile clients, or the in-process event bus - without
dragging a framework along with them.
"""

import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum


def new_id():
    return uuid.uuid4().hex


def now():
    return time.time()


class TaskStatus(str, Enum):
    PENDING = "pending"          # accepted, not started
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def is_terminal(self):
        return self in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED)


class StepStatus(str, Enum):
    PENDING = "pending"
    ACTIVE = "active"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"


class RiskLevel(str, Enum):
    """How much damage an action could do if it were wrong.

    Risk is a property of the capability, not of the agent asking for it, and
    it is what decides whether the user is interrupted.
    """
    LOW = "low"                  # reading public or harmless information
    MEDIUM = "medium"            # reading private data, sending a message
    HIGH = "high"                # writing, deploying, spending
    CRITICAL = "critical"        # destroying data or changing who has access

    @property
    def rank(self):
        return {"low": 0, "medium": 1, "high": 2, "critical": 3}[self.value]


class Decision(str, Enum):
    """What the policy engine says about one capability request."""
    ALLOW = "allow"
    REQUIRE_APPROVAL = "require_approval"
    DENY = "deny"


class ApprovalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    DECLINED = "declined"
    EXPIRED = "expired"


class PermissionStatus(str, Enum):
    ACTIVE = "active"
    EXPIRED = "expired"
    REVOKED = "revoked"


class DeviceStatus(str, Enum):
    ONLINE = "online"
    OFFLINE = "offline"


class HelperStatus(str, Enum):
    IDLE = "idle"
    WORKING = "working"
    OFFLINE = "offline"
    QUARANTINED = "quarantined"


class EventType(str, Enum):
    """Observable actions only. Never model reasoning."""
    TASK_CREATED = "task_created"
    TASK_STARTED = "task_started"
    TASK_COMPLETED = "task_completed"
    TASK_FAILED = "task_failed"
    TASK_CANCELLED = "task_cancelled"
    STEP_STARTED = "step_started"
    STEP_FINISHED = "step_finished"
    HELPER_SELECTED = "helper_selected"
    PERMISSION_GRANTED = "permission_granted"
    PERMISSION_REVOKED = "permission_revoked"
    APPROVAL_REQUESTED = "approval_requested"
    APPROVAL_RESOLVED = "approval_resolved"
    CAPABILITY_REQUESTED = "capability_requested"
    CAPABILITY_DENIED = "capability_denied"
    POLICY_CHANGED = "policy_changed"
    AGENT_REGISTERED = "agent_registered"
    AGENT_OFFLINE = "agent_offline"
    AGENT_QUARANTINED = "agent_quarantined"
    DEVICE_CONNECTED = "device_connected"
    DEVICE_DISCONNECTED = "device_disconnected"
    EMERGENCY_STOP = "emergency_stop"
    NOTE = "note"


@dataclass
class Device:
    """A machine VAVE can work on."""
    id: str = field(default_factory=new_id)
    name: str = ""
    kind: str = "computer"          # computer, phone, server, nas, cloud
    platform: str = ""              # windows, darwin, linux, android, ios
    status: DeviceStatus = DeviceStatus.ONLINE
    last_seen: float = field(default_factory=now)
    token_hash: str = ""            # sha256 of the device's API token
    paired_at: float = 0.0
    token_expires_at: float = 0.0   # unix epoch timestamp when token expires (0.0 = never)
    capabilities: list = field(default_factory=list)   # what this machine can do

    def can(self, capability):
        return capability in self.capabilities

    def is_stale(self, seconds, at=None):
        """True when this device has not been heard from recently enough."""
        at = at if at is not None else now()
        return (at - self.last_seen) > seconds

    @property
    def is_paired(self):
        return bool(self.token_hash)

    @property
    def is_expired(self):
        return bool(self.token_expires_at and now() > self.token_expires_at)

    def to_dict(self):
        data = _serialise(self)
        # A device's credential never leaves the backend, not even hashed.
        data.pop("token_hash", None)
        return data


@dataclass
class Helper:
    """An AI agent. 'Framework' is an implementation detail the UI hides.

    An agent advertises what it can do and reports how it is doing. Health is
    recorded rather than guessed: heartbeats say it is alive, and the counts
    and latencies come from work it actually did.
    """
    id: str = field(default_factory=new_id)
    name: str = ""
    framework: str = "native"       # native, http, mcp, langgraph, crewai
    device_id: str = ""
    status: HelperStatus = HelperStatus.IDLE
    capabilities: list = field(default_factory=list)
    current_task_id: str = ""
    last_active: float = field(default_factory=now)
    version: str = ""
    endpoint: str = ""              # where a remote agent is reached
    metadata: dict = field(default_factory=dict)
    enabled: bool = True            # switched off by a person, not by health
    last_heartbeat: float = 0.0
    success_count: int = 0
    error_count: int = 0
    latencies: list = field(default_factory=list)      # milliseconds, most recent last

    def can(self, capability):
        return capability in self.capabilities

    @property
    def is_available(self):
        """Whether this agent may be given work right now."""
        return (self.enabled
                and self.status in (HelperStatus.IDLE, HelperStatus.WORKING))

    @property
    def error_rate(self):
        total = self.success_count + self.error_count
        return round(self.error_count / total, 3) if total else 0.0

    @property
    def p95_latency_ms(self):
        """The slow end of recent work, which is what a person notices."""
        if not self.latencies:
            return 0
        ordered = sorted(self.latencies)
        index = min(len(ordered) - 1, int(round(0.95 * (len(ordered) - 1))))
        return int(ordered[index])

    def is_stale(self, seconds, at=None):
        """True when a heartbeat was expected by now and did not arrive."""
        if not self.last_heartbeat:
            return False        # never sent one; silence is not evidence yet
        at = at if at is not None else now()
        return (at - self.last_heartbeat) > seconds

    def record_result(self, ok, latency_ms=None, keep=50):
        if ok:
            self.success_count += 1
        else:
            self.error_count += 1
        if latency_ms is not None:
            self.latencies = (self.latencies + [int(latency_ms)])[-keep:]

    def health(self):
        """A small snapshot for a monitoring screen."""
        return {
            "id": self.id,
            "name": self.name,
            "status": self.status.value,
            "enabled": self.enabled,
            "version": self.version,
            "last_heartbeat": self.last_heartbeat,
            "success_count": self.success_count,
            "error_count": self.error_count,
            "error_rate": self.error_rate,
            "p95_latency_ms": self.p95_latency_ms,
        }

    def to_dict(self):
        data = _serialise(self)
        data["error_rate"] = self.error_rate
        data["p95_latency_ms"] = self.p95_latency_ms
        return data


@dataclass
class TaskStep:
    """One observable step, phrased for a person: 'Finding relevant files'.

    Steps form a graph rather than a straight line: `depends_on` holds the
    positions that must finish first, so independent steps can run at the same
    time without the caller having to say so.
    """
    id: str = field(default_factory=new_id)
    task_id: str = ""
    position: int = 0
    label: str = ""
    status: StepStatus = StepStatus.PENDING
    detail: str = ""
    depends_on: list = field(default_factory=list)   # positions, not ids
    agent_id: str = ""              # who should do it, when it matters
    capability: str = ""            # what access it needs, if any
    attempts: int = 0               # how many times this has been tried
    artifacts: list = field(default_factory=list)    # files or links it produced

    @property
    def is_finished(self):
        return self.status in (StepStatus.DONE, StepStatus.SKIPPED)

    def to_dict(self):
        return _serialise(self)


@dataclass
class StepResult:
    """What a step produced.

    Adapters may answer with plain text, which is normalised into one of
    these, so the orchestrator has one shape to handle whether the work ran in
    this process or on another machine.
    """
    ok: bool = True
    output: str = ""
    artifacts: list = field(default_factory=list)     # paths, URLs, ids
    error: str = ""

    @classmethod
    def of(cls, value):
        """Accept a StepResult, a dict, or plain text from any adapter."""
        if isinstance(value, cls):
            return value
        if isinstance(value, dict):
            return cls(ok=bool(value.get("ok", True)),
                       output=str(value.get("output", "")),
                       artifacts=list(value.get("artifacts", []) or []),
                       error=str(value.get("error", "")))
        return cls(output=str(value or "").strip() or "Done.")

    def to_dict(self):
        return _serialise(self)


@dataclass
class Task:
    """A goal the user asked for, in their own words."""
    id: str = field(default_factory=new_id)
    goal: str = ""
    status: TaskStatus = TaskStatus.PENDING
    helper_id: str = ""
    device_id: str = ""
    summary: str = ""               # plain-language outcome once finished
    checkpoint: dict = field(default_factory=dict)
    created_at: float = field(default_factory=now)
    updated_at: float = field(default_factory=now)

    def to_dict(self):
        return _serialise(self)


@dataclass
class Permission:
    """Task-scoped, time-limited access. Least privilege by construction."""
    id: str = field(default_factory=new_id)
    task_id: str = ""
    resource: str = ""
    actions: list = field(default_factory=list)   # read, write, execute
    device_id: str = ""
    agent_id: str = ""              # who this was granted to, when an agent asked
    status: PermissionStatus = PermissionStatus.ACTIVE
    granted_at: float = field(default_factory=now)
    expires_at: float = 0.0

    def is_valid(self, at=None):
        at = at if at is not None else now()
        return self.status == PermissionStatus.ACTIVE and at < self.expires_at

    def allows(self, action, at=None):
        return self.is_valid(at) and action in self.actions

    def to_dict(self):
        data = _serialise(self)
        data["seconds_remaining"] = max(0, int(self.expires_at - now()))
        return data


@dataclass
class Approval:
    """A consequential action held until the user decides.

    When an approval carries a capability, approving it is what releases that
    access - the user's decision and the grant are the same event.
    """
    id: str = field(default_factory=new_id)
    task_id: str = ""
    action: str = ""                # short label: "Send email"
    question: str = ""              # what VAVE is about to do
    reason: str = ""                # why
    impact: str = ""                # what changes
    status: ApprovalStatus = ApprovalStatus.PENDING
    created_at: float = field(default_factory=now)
    resolved_at: float = 0.0
    capability: str = ""            # what access this releases, if any
    resource: str = ""              # what it applies to
    risk: RiskLevel = RiskLevel.MEDIUM
    agent_id: str = ""              # who asked
    seconds: int = 0                # how long the grant should last

    def to_dict(self):
        return _serialise(self)


@dataclass
class PolicyRule:
    """One allow/deny/ask rule.

    Patterns match capability strings and support a trailing wildcard, so
    `google.gmail.*` covers every Gmail capability. An empty agent, task or
    resource means "any", which is what makes a rule general or specific.
    """
    id: str = field(default_factory=new_id)
    capability: str = "*"
    decision: Decision = Decision.ALLOW
    agent_id: str = ""
    task_id: str = ""
    resource: str = ""
    reason: str = ""
    created_at: float = field(default_factory=now)

    @property
    def specificity(self):
        """How narrowly this rule is aimed. The narrowest rule wins."""
        score = 0
        if self.capability != "*":
            score += 4 if not self.capability.endswith("*") else 2
        if self.agent_id:
            score += 2
        if self.task_id:
            score += 2
        if self.resource:
            score += 1
        return score

    def matches(self, capability, agent_id="", task_id="", resource=""):
        if self.agent_id and self.agent_id != agent_id:
            return False
        if self.task_id and self.task_id != task_id:
            return False
        if self.resource and self.resource != resource:
            return False
        return matches_pattern(self.capability, capability)

    def to_dict(self):
        return _serialise(self)


def matches_pattern(pattern, capability):
    """`*` matches everything, `a.b.*` matches the a.b namespace."""
    if pattern in ("*", capability):
        return True
    if pattern.endswith(".*"):
        return capability.startswith(pattern[:-1])
    return False


@dataclass
class ActivityEvent:
    """One line of the unified timeline, written in plain English.

    The message is what a person reads; the fields beside it are what a client
    filters and groups on. Observable actions only - never model reasoning.
    """
    id: str = field(default_factory=new_id)
    task_id: str = ""
    type: EventType = EventType.NOTE
    message: str = ""
    actor: str = "VAVE"
    device_id: str = ""
    timestamp: float = field(default_factory=now)
    metadata: dict = field(default_factory=dict)
    agent_id: str = ""              # which agent did it
    capability: str = ""            # what access was involved
    risk: str = ""                  # how consequential it was
    approval_id: str = ""           # the decision this belongs to
    result: str = ""                # ok, failed, denied, cancelled

    def to_dict(self):
        return _serialise(self)


def _serialise(item):
    """dataclass -> JSON-safe dict, with enums flattened to their values."""
    data = asdict(item)
    for key, value in data.items():
        if isinstance(value, Enum):
            data[key] = value.value
    return data


# -- SQLite helpers ---------------------------------------------------------
# Lists and dicts are stored as JSON text; these keep that in one place.

def dumps(value):
    return json.dumps(value or ([] if isinstance(value, list) else {}))


def loads(text, fallback=None):
    if not text:
        return fallback if fallback is not None else {}
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return fallback if fallback is not None else {}
