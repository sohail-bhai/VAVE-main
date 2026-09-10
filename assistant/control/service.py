"""The control plane itself.

This is the layer that owns coordination: it accepts a goal, records the steps,
picks a helper by capability, grants narrow time-limited access, holds
consequential actions for approval, and writes a readable trail of what
happened. VAVE owns this state - external frameworks plug into it rather than
replacing it.

Everything here is synchronous and thread-safe so it can be driven equally from
the desktop app, the HTTP API, or a background worker.
"""

import logging
import platform
import socket
import threading

from assistant.control.capabilities import describe, risk_for
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
    RiskLevel,
    StepStatus,
    Task,
    TaskStatus,
    TaskStep,
    now,
)
from assistant.control.policy import PolicyEngine
from assistant.control.secrets import SecretStore
from assistant.control.store import ControlStore

logger = logging.getLogger(__name__)

# How long task-scoped access lasts unless the caller says otherwise.
DEFAULT_PERMISSION_SECONDS = 30 * 60

# An agent that has sent heartbeats and then goes quiet for this long is
# treated as gone. Silence from an agent that never sent one proves nothing.
AGENT_STALE_SECONDS = 90

# Devices report less often than agents; a phone is asleep most of the day.
DEVICE_STALE_SECONDS = 5 * 60


class ControlPlane:
    """Coordinates goals across helpers and devices, and keeps the user in control."""

    def __init__(self, store=None, event_bus=None):
        self.store = store or ControlStore()
        self.policy = PolicyEngine(self.store)
        self.secrets = SecretStore(self.store)
        self.event_bus = event_bus
        self._lock = threading.RLock()
        self._subscribers = []
        self._stopped = False
        self.local_device = self._register_local_device()

    def close(self):
        """Release underlying store and resources."""
        with self._lock:
            if hasattr(self, "store") and self.store:
                try:
                    self.store.close()
                except Exception:
                    pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    # -- subscriptions ------------------------------------------------------

    def subscribe(self, callback):
        """Register a callback for activity events. Returns an unsubscribe fn."""
        with self._lock:
            self._subscribers.append(callback)

        def unsubscribe():
            with self._lock:
                if callback in self._subscribers:
                    self._subscribers.remove(callback)

        return unsubscribe

    def _publish(self, event):
        with self._lock:
            subscribers = list(self._subscribers)

        for callback in subscribers:
            try:
                callback(event)
            except Exception:
                # A broken listener must never derail the work being reported.
                logger.exception("Activity subscriber failed")

    # -- devices ------------------------------------------------------------

    def _register_local_device(self):
        """Make sure the machine VAVE runs on is always a known device."""
        name = socket.gethostname() or "This computer"
        for device in self.store.list_devices():
            if device.name == name and device.kind == "computer":
                device.status = DeviceStatus.ONLINE
                device.last_seen = now()
                return self.store.save_device(device)

        device = Device(name=name, kind="computer",
                        platform=platform.system().lower(),
                        status=DeviceStatus.ONLINE)
        return self.store.save_device(device)

    def register_device(self, name, kind="computer", platform_name="",
                        capabilities=None):
        device = Device(name=name, kind=kind, platform=platform_name,
                        capabilities=list(capabilities or []))
        self.store.save_device(device)
        self.record(f"{device.name} connected.", EventType.DEVICE_CONNECTED,
                    metadata={"device_id": device.id})
        return device

    def device_heartbeat(self, device_id, capabilities=None):
        """A device says it is still here, and what it can do."""
        device = self.store.get_device(device_id)
        if device is None:
            return None

        was_offline = device.status == DeviceStatus.OFFLINE
        device.last_seen = now()
        device.status = DeviceStatus.ONLINE
        if capabilities is not None:
            device.capabilities = list(capabilities)
        self.store.save_device(device)

        if was_offline:
            self.record(f"{device.name} is back.", EventType.DEVICE_CONNECTED,
                        metadata={"device_id": device.id})
        return device

    def list_devices(self):
        self.sweep()
        return self.store.list_devices()

    # -- helpers ------------------------------------------------------------

    def register_helper(self, name, capabilities, framework="native", device_id=None,
                        version="", endpoint="", metadata=None):
        """Add an AI agent and advertise what it can do."""
        helper = Helper(name=name, framework=framework,
                        device_id=device_id or self.local_device.id,
                        capabilities=list(capabilities), version=version,
                        endpoint=endpoint, metadata=dict(metadata or {}),
                        last_heartbeat=now())
        self.store.save_helper(helper)
        self.record(f"{helper.name} is available"
                    + (f" ({version})." if version else "."),
                    EventType.AGENT_REGISTERED, metadata={"agent_id": helper.id})
        return helper

    def list_helpers(self):
        self.sweep()
        return self.store.list_helpers()

    def get_helper(self, helper_id):
        return self.store.get_helper(helper_id)

    def heartbeat(self, helper_id, latency_ms=None, ok=None, status=None):
        """An agent reports that it is alive, and how its last work went."""
        helper = self.store.get_helper(helper_id)
        if helper is None:
            return None

        was_offline = helper.status == HelperStatus.OFFLINE
        helper.last_heartbeat = now()
        helper.last_active = now()

        if ok is not None:
            helper.record_result(ok, latency_ms)

        if helper.status != HelperStatus.QUARANTINED:
            if status is not None:
                helper.status = HelperStatus(status)
            elif was_offline:
                helper.status = HelperStatus.IDLE

        self.store.save_helper(helper)

        if was_offline and helper.status != HelperStatus.OFFLINE:
            self.record(f"{helper.name} is back.", EventType.AGENT_REGISTERED,
                        metadata={"agent_id": helper.id})
        return helper

    def set_helper_enabled(self, helper_id, enabled):
        """Switch an agent off without forgetting it or losing its history."""
        helper = self.store.get_helper(helper_id)
        if helper is None:
            return None

        helper.enabled = bool(enabled)
        self.store.save_helper(helper)
        self.record(f"{helper.name} was turned {'on' if enabled else 'off'}.",
                    EventType.NOTE, metadata={"agent_id": helper.id})
        return helper

    def set_helper_working(self, helper_id, task_id=""):
        helper = self.store.get_helper(helper_id)
        if helper is None:
            return None
        helper.status = HelperStatus.WORKING
        helper.current_task_id = task_id
        self.store.save_helper(helper)
        return helper

    def set_helper_idle(self, helper_id):
        helper = self.store.get_helper(helper_id)
        if helper is None:
            return None
        if helper.status == HelperStatus.WORKING:
            helper.status = HelperStatus.IDLE
            helper.current_task_id = ""
            self.store.save_helper(helper)
        return helper

    def sweep(self, at=None):
        """Mark agents and devices offline once they have gone quiet.

        Health is observed, not assumed: this only acts on things that used to
        report and then stopped.
        """
        at = at if at is not None else now()
        gone = []

        for helper in self.store.list_helpers():
            if helper.status in (HelperStatus.OFFLINE, HelperStatus.QUARANTINED):
                continue
            if helper.is_stale(AGENT_STALE_SECONDS, at=at):
                helper.status = HelperStatus.OFFLINE
                helper.current_task_id = ""
                self.store.save_helper(helper)
                self.record(f"{helper.name} stopped responding.",
                            EventType.AGENT_OFFLINE, agent_id=helper.id)
                gone.append(helper)

        for device in self.store.list_devices():
            if device.status == DeviceStatus.OFFLINE:
                continue
            if device.id == self.local_device.id:
                continue
            if device.is_stale(DEVICE_STALE_SECONDS, at=at):
                device.status = DeviceStatus.OFFLINE
                self.store.save_device(device)
                self.record(f"{device.name} went offline.",
                            EventType.DEVICE_DISCONNECTED,
                            metadata={"device_id": device.id})
                gone.append(device)

        return gone

    def agent_health(self):
        """One row per agent for a monitoring screen."""
        self.sweep()
        return [helper.health() for helper in self.store.list_helpers()]

    def kill_helper(self, helper_id, reason=""):
        """Stop an agent now: its work, its access, and any future request.

        Quarantine is deliberate rather than automatic. Nothing is deleted, so
        the timeline still shows what the agent did before it was stopped.
        """
        helper = self.store.get_helper(helper_id)
        if helper is None:
            return None

        cancelled = ""
        if helper.current_task_id:
            task = self.store.get_task(helper.current_task_id)
            if task is not None and not task.status.is_terminal:
                self.cancel_task(task.id, f"{helper.name} was stopped.")
                cancelled = task.id

        revoked = self._revoke_agent_permissions(helper_id, "the agent was stopped")

        helper.status = HelperStatus.QUARANTINED
        helper.enabled = False
        helper.current_task_id = ""
        self.store.save_helper(helper)

        self.record(f"Stopped {helper.name} and removed its access."
                    + (f" {reason}" if reason else ""),
                    EventType.AGENT_QUARANTINED,
                    metadata={"agent_id": helper.id, "cancelled_task": cancelled,
                              "permissions_revoked": revoked})
        return helper

    def _revoke_agent_permissions(self, agent_id, reason):
        revoked = 0
        for permission in self.store.list_permissions(active_only=True,
                                                      agent_id=agent_id):
            permission.status = PermissionStatus.REVOKED
            self.store.save_permission(permission)
            revoked += 1
        if revoked:
            self.record(f"Removed {revoked} temporary permission"
                        f"{'s' if revoked != 1 else ''} because {reason}.",
                        EventType.PERMISSION_REVOKED, metadata={"agent_id": agent_id})
        return revoked

    def find_helper_for(self, capability):
        """Pick an available helper that advertises `capability`.

        The user asks for an outcome; this is what turns that into a worker.
        Quarantined and offline helpers are never selected.
        """
        self.sweep()
        candidates = [
            helper for helper in self.store.list_helpers()
            if helper.can(capability) and helper.is_available
        ]
        if not candidates:
            return None
        # Prefer an idle helper, then the least recently used one.
        candidates.sort(key=lambda h: (h.status != HelperStatus.IDLE, h.last_active))
        return candidates[0]

    def quarantine_helper(self, helper_id, reason=""):
        """Isolate a misbehaving helper while keeping it visible."""
        helper = self.store.get_helper(helper_id)
        if helper is None:
            return None
        helper.status = HelperStatus.QUARANTINED
        self.store.save_helper(helper)
        self.record(f"Stopped using {helper.name}." + (f" {reason}" if reason else ""),
                    EventType.NOTE)
        return helper

    # -- tasks --------------------------------------------------------------

    def create_task(self, goal, steps=None, capability=None):
        """Accept a goal and lay out the steps the user will see."""
        if self._stopped:
            raise RuntimeError("VAVE is stopped. Reset before starting new work.")

        task = Task(goal=goal, status=TaskStatus.PENDING,
                    device_id=self.local_device.id)

        helper = self.find_helper_for(capability) if capability else None
        if helper is not None:
            task.helper_id = helper.id

        self.store.save_task(task)

        for position, entry in enumerate(steps or []):
            self.store.save_step(self._build_step(task.id, position, entry))

        self.record(f"Started working on: {goal}", EventType.TASK_CREATED, task_id=task.id)

        if helper is not None:
            self.record(f"Asked {helper.name} to help.", EventType.HELPER_SELECTED,
                        task_id=task.id)

        return task

    @staticmethod
    def _build_step(task_id, position, entry):
        """A step is either a label or a dict describing the graph node.

        A plain list of labels is a sequence, which is what a caller listing
        steps means. Only a caller (or the planner) that says `depends_on`
        opts into steps running at the same time.
        """
        if isinstance(entry, str):
            return TaskStep(task_id=task_id, position=position, label=entry,
                            depends_on=[position - 1] if position else [])

        return TaskStep(task_id=task_id, position=position,
                        label=entry.get("label", ""),
                        depends_on=[int(value) for value in
                                    entry.get("depends_on", []) or []],
                        agent_id=entry.get("agent_id", ""),
                        capability=entry.get("capability", ""))

    def get_task(self, task_id):
        return self.store.get_task(task_id)

    def list_tasks(self, limit=50, active_only=False):
        return self.store.list_tasks(limit=limit, active_only=active_only)

    def task_detail(self, task_id):
        """A task with its steps and progress, ready for a client to render."""
        task = self.store.get_task(task_id)
        if task is None:
            return None

        steps = self.store.list_steps(task_id)
        done = sum(1 for step in steps if step.status == StepStatus.DONE)

        detail = task.to_dict()
        detail["steps"] = [step.to_dict() for step in steps]
        detail["progress"] = round(done / len(steps), 2) if steps else 0.0
        detail["current_step"] = next(
            (step.label for step in steps if step.status == StepStatus.ACTIVE), "")
        return detail

    def start_step(self, task_id, position):
        """Mark a step as the one being worked on now."""
        steps = self.store.list_steps(task_id)
        target = next((step for step in steps if step.position == position), None)
        if target is None:
            return None

        for step in steps:
            if step.status == StepStatus.ACTIVE:
                step.status = StepStatus.DONE
                self.store.save_step(step)

        target.status = StepStatus.ACTIVE
        self.store.save_step(target)

        task = self.store.get_task(task_id)
        if task and task.status == TaskStatus.PENDING:
            self._set_task_status(task, TaskStatus.RUNNING)
            self.record(f"Started: {task.goal}", EventType.TASK_STARTED,
                        task_id=task_id, agent_id=task.helper_id)

        self.record(target.label, EventType.STEP_STARTED, task_id=task_id,
                    agent_id=target.agent_id, capability=target.capability,
                    metadata={"position": target.position})
        return target

    def delegate(self, task_id, label, capability="", agent_id="", after=None):
        """Hand part of a task to another agent, under the same task.

        The new step joins the graph rather than starting a second task, so
        one goal keeps one timeline, one set of approvals and one summary.
        """
        task = self.store.get_task(task_id)
        if task is None:
            return None

        steps = self.store.list_steps(task_id)
        position = max((step.position for step in steps), default=-1) + 1

        if not agent_id and capability:
            helper = self.find_helper_for(capability)
            if helper is None:
                self.record(f"Nobody available to {label.lower()}.", EventType.NOTE,
                            task_id=task_id)
                return None
            agent_id = helper.id

        depends_on = ([int(value) for value in after] if after is not None
                      else [step.position for step in steps if not step.is_finished])

        step = TaskStep(task_id=task_id, position=position, label=label,
                        depends_on=depends_on, agent_id=agent_id,
                        capability=capability)
        self.store.save_step(step)

        helper = self.store.get_helper(agent_id) if agent_id else None
        self.record(f"Asked {helper.name if helper else 'another agent'} to {label.lower()}.",
                    EventType.HELPER_SELECTED, task_id=task_id,
                    metadata={"agent_id": agent_id, "position": position})
        return step

    def record_attempt(self, task_id, position):
        """Count one try at a step, so retries are visible rather than hidden."""
        steps = self.store.list_steps(task_id)
        target = next((step for step in steps if step.position == position), None)
        if target is None:
            return None
        target.attempts += 1
        self.store.save_step(target)
        return target

    def finish_step(self, task_id, position, detail="", failed=False,
                    artifacts=None):
        steps = self.store.list_steps(task_id)
        target = next((step for step in steps if step.position == position), None)
        if target is None:
            return None

        target.status = StepStatus.FAILED if failed else StepStatus.DONE
        target.detail = detail
        if artifacts:
            target.artifacts = list(artifacts)
        self.store.save_step(target)

        self.record(detail or f"Finished: {target.label}",
                    EventType.STEP_FINISHED, task_id=task_id,
                    agent_id=target.agent_id, capability=target.capability,
                    result="failed" if failed else "ok",
                    metadata={"position": target.position,
                              "attempts": target.attempts})
        return target

    def complete_task(self, task_id, summary=""):
        task = self.store.get_task(task_id)
        if task is None:
            return None

        for step in self.store.list_steps(task_id):
            if step.status in (StepStatus.PENDING, StepStatus.ACTIVE):
                step.status = StepStatus.DONE
                self.store.save_step(step)

        task.summary = summary
        self._set_task_status(task, TaskStatus.COMPLETED)
        self._release_task_permissions(task_id, "the task finished")
        if task.helper_id:
            self.set_helper_idle(task.helper_id)
        self.record(summary or "Done.", EventType.TASK_COMPLETED, task_id=task_id,
                    agent_id=task.helper_id, result="ok")
        return task

    def fail_task(self, task_id, reason=""):
        """Record an honest failure. Never dress this up as success."""
        task = self.store.get_task(task_id)
        if task is None:
            return None

        task.summary = reason
        self._set_task_status(task, TaskStatus.FAILED)
        self._release_task_permissions(task_id, "the task stopped")
        if task.helper_id:
            self.set_helper_idle(task.helper_id)
        self.record(reason or "Couldn't finish this step.",
                    EventType.TASK_FAILED, task_id=task_id,
                    agent_id=task.helper_id, result="failed")
        return task

    def cancel_task(self, task_id, reason="You stopped this."):
        task = self.store.get_task(task_id)
        if task is None or task.status.is_terminal:
            return task

        self._set_task_status(task, TaskStatus.CANCELLED)
        self._release_task_permissions(task_id, "the task was stopped")
        if task.helper_id:
            self.set_helper_idle(task.helper_id)
        self.record(reason, EventType.TASK_CANCELLED, task_id=task_id,
                    agent_id=task.helper_id, result="cancelled")
        return task

    def interrupted_tasks(self):
        """Tasks that were still running when the process last stopped.

        Nothing marks these as failed on the way down - the process may have
        been killed - so they are found on the way back up instead.
        """
        return [task for task in self.store.list_tasks(limit=500, active_only=True)
                if task.status in (TaskStatus.RUNNING, TaskStatus.PENDING)]

    def save_checkpoint(self, task_id, checkpoint):
        """Persist enough state to resume this task after an interruption."""
        task = self.store.get_task(task_id)
        if task is None:
            return None
        task.checkpoint = checkpoint
        task.updated_at = now()
        return self.store.save_task(task)

    def _set_task_status(self, task, status):
        task.status = status
        task.updated_at = now()
        self.store.save_task(task)

    # -- permissions --------------------------------------------------------

    def grant(self, resource, actions, task_id="", seconds=DEFAULT_PERMISSION_SECONDS,
              device_id="", agent_id=""):
        """Grant narrow, time-limited access for one task."""
        permission = Permission(
            task_id=task_id, resource=resource, actions=list(actions),
            device_id=device_id or self.local_device.id, agent_id=agent_id,
            expires_at=now() + seconds,
        )
        self.store.save_permission(permission)

        minutes = max(1, int(seconds // 60))
        self.record(
            f"Allowed {' and '.join(actions)} access to {resource} for {minutes} minutes.",
            EventType.PERMISSION_GRANTED, task_id=task_id, agent_id=agent_id,
            capability=actions[0] if actions else "", result="granted")
        return permission

    def check(self, resource, action, task_id=""):
        """Is this action allowed on this resource right now?"""
        moment = now()
        for permission in self.store.list_permissions(active_only=True):
            if permission.resource != resource:
                continue
            if task_id and permission.task_id and permission.task_id != task_id:
                continue
            if permission.allows(action, moment):
                return True
        return False

    def revoke(self, permission_id, reason=""):
        permission = self.store.get_permission(permission_id)
        if permission is None:
            return None

        permission.status = PermissionStatus.REVOKED
        self.store.save_permission(permission)
        self.record(f"Removed access to {permission.resource}."
                    + (f" {reason}" if reason else ""),
                    EventType.PERMISSION_REVOKED, task_id=permission.task_id)
        return permission

    def expire_permissions(self):
        """Mark lapsed grants expired. Safe to call repeatedly."""
        moment = now()
        expired = []
        for permission in self.store.list_permissions(active_only=True):
            if moment >= permission.expires_at:
                permission.status = PermissionStatus.EXPIRED
                self.store.save_permission(permission)
                expired.append(permission)
        return expired

    def list_permissions(self, active_only=True):
        self.expire_permissions()
        return self.store.list_permissions(active_only=active_only)

    def _release_task_permissions(self, task_id, reason):
        for permission in self.store.list_permissions(active_only=True, task_id=task_id):
            permission.status = PermissionStatus.REVOKED
            self.store.save_permission(permission)

    # -- capabilities -------------------------------------------------------

    def request_capability(self, capability, agent_id="", task_id="", resource="",
                           seconds=DEFAULT_PERMISSION_SECONDS, reason=""):
        """Ask for one named capability. This is how an agent gets access.

        Returns a dict describing what happened: `granted` with a permission,
        `waiting` with the approval the user must answer, or `denied`. The
        caller never gets access as a side effect of asking - it holds a
        permission or it holds nothing.
        """
        if self._stopped:
            raise RuntimeError("VAVE is stopped. Reset before starting new work.")

        judgement = self.policy.evaluate(capability, agent_id=agent_id,
                                         task_id=task_id, resource=resource)
        target = resource or capability

        self.record(f"Asked to {describe(capability).lower()}.",
                    EventType.CAPABILITY_REQUESTED, task_id=task_id,
                    agent_id=agent_id, capability=capability,
                    risk=judgement.risk.value,
                    metadata={"decision": judgement.decision.value})

        if judgement.denied:
            self.record(f"Refused: {describe(capability).lower()}. {judgement.reason}",
                        EventType.CAPABILITY_DENIED, task_id=task_id,
                        agent_id=agent_id, capability=capability,
                        risk=judgement.risk.value, result="denied")
            return {"status": "denied", "judgement": judgement.to_dict(),
                    "permission": None, "approval": None}

        if judgement.needs_approval:
            approval = self.request_approval(
                action=describe(capability),
                question=f"Can I {describe(capability).lower()}?",
                reason=reason or judgement.reason,
                impact=f"This grants {capability} on {target} for "
                       f"{max(1, int(seconds // 60))} minutes.",
                task_id=task_id, capability=capability, resource=target,
                risk=judgement.risk, agent_id=agent_id, seconds=seconds)
            return {"status": "waiting", "judgement": judgement.to_dict(),
                    "permission": None, "approval": approval.to_dict()}

        permission = self.grant(target, [capability], task_id=task_id,
                                seconds=seconds, agent_id=agent_id)
        return {"status": "granted", "judgement": judgement.to_dict(),
                "permission": permission.to_dict(), "approval": None}

    def has_capability(self, capability, task_id="", resource=""):
        """Whether a live grant currently authorises this capability."""
        return self.check(resource or capability, capability, task_id=task_id)

    def capability_risk(self, capability):
        return risk_for(capability)

    def add_policy_rule(self, capability, decision, agent_id="", task_id="",
                        resource="", reason=""):
        rule = self.policy.add_rule(capability, decision, agent_id=agent_id,
                                    task_id=task_id, resource=resource, reason=reason)
        self.record(f"Policy: {rule.decision.value} {rule.capability}.",
                    EventType.POLICY_CHANGED, metadata={"rule_id": rule.id})
        return rule

    def list_policy_rules(self):
        return self.policy.list_rules()

    def remove_policy_rule(self, rule_id):
        rule = self.policy.remove_rule(rule_id)
        if rule is not None:
            self.record(f"Policy rule removed: {rule.capability}.",
                        EventType.POLICY_CHANGED, metadata={"rule_id": rule_id})
        return rule

    # -- approvals ----------------------------------------------------------

    def request_approval(self, action, question, reason="", impact="", task_id="",
                         capability="", resource="", risk=RiskLevel.MEDIUM,
                         agent_id="", seconds=DEFAULT_PERMISSION_SECONDS):
        """Hold a consequential action until the user decides.

        An approval that carries a capability is a held grant: approving it
        releases exactly that access, so the decision and the permission are
        the same event rather than two things that can drift apart.
        """
        approval = Approval(task_id=task_id, action=action, question=question,
                            reason=reason, impact=impact, capability=capability,
                            resource=resource, risk=risk, agent_id=agent_id,
                            seconds=seconds)
        self.store.save_approval(approval)

        if task_id:
            task = self.store.get_task(task_id)
            if task is not None and not task.status.is_terminal:
                self._set_task_status(task, TaskStatus.WAITING_APPROVAL)

        self.record(f"Waiting for your approval: {question}",
                    EventType.APPROVAL_REQUESTED, task_id=task_id,
                    agent_id=agent_id, capability=capability, risk=risk.value,
                    approval_id=approval.id)
        return approval

    def resolve_approval(self, approval_id, approved):
        approval = self.store.get_approval(approval_id)
        if approval is None or approval.status != ApprovalStatus.PENDING:
            return None

        approval.status = ApprovalStatus.APPROVED if approved else ApprovalStatus.DECLINED
        approval.resolved_at = now()
        self.store.save_approval(approval)

        # Approving a held capability is what releases it. Nothing else does.
        if approved and approval.capability:
            self.grant(approval.resource or approval.capability,
                       [approval.capability], task_id=approval.task_id,
                       seconds=approval.seconds or DEFAULT_PERMISSION_SECONDS,
                       agent_id=approval.agent_id)

        if approval.task_id:
            task = self.store.get_task(approval.task_id)
            if task is not None and task.status == TaskStatus.WAITING_APPROVAL:
                self._set_task_status(
                    task, TaskStatus.RUNNING if approved else TaskStatus.CANCELLED)

        self.record(
            f"You approved: {approval.action}" if approved
            else f"You declined: {approval.action}",
            EventType.APPROVAL_RESOLVED, task_id=approval.task_id,
            agent_id=approval.agent_id, capability=approval.capability,
            risk=approval.risk.value, approval_id=approval.id,
            result="approved" if approved else "declined",
            metadata={"approved": approved})
        return approval

    def list_approvals(self, pending_only=True):
        return self.store.list_approvals(pending_only=pending_only)

    # -- activity -----------------------------------------------------------

    def record(self, message, event_type=EventType.NOTE, task_id="",
               actor="VAVE", metadata=None, agent_id="", capability="",
               risk="", approval_id="", result=""):
        """Write one plain-English line to the timeline and notify listeners.

        The message is for a person; the fields beside it are what a client
        filters on - which agent, which capability, how risky, which decision,
        and how it turned out.
        """
        # Nothing should put a credential here; anything that does is caught
        # before it reaches the timeline, a log, or a phone.
        message = self.secrets.redact(message)

        event = ActivityEvent(task_id=task_id, type=event_type, message=message,
                              actor=actor, device_id=self.local_device.id,
                              metadata=metadata or {}, agent_id=agent_id,
                              capability=capability, risk=risk,
                              approval_id=approval_id, result=result)
        self.store.save_event(event)
        self._publish(event)
        return event

    def list_events(self, limit=100, task_id=None, types=None):
        return self.store.list_events(limit=limit, task_id=task_id, types=types)

    # -- emergency stop -----------------------------------------------------

    def emergency_stop(self):
        """Stop active work, revoke access, and refuse new tasks.

        This never deletes user data. It cancels what is running, invalidates
        every temporary grant, and latches until `resume()` is called.
        """
        cancelled, revoked = 0, 0

        for task in self.store.list_tasks(limit=500, active_only=True):
            self._set_task_status(task, TaskStatus.CANCELLED)
            cancelled += 1

        for permission in self.store.list_permissions(active_only=True):
            permission.status = PermissionStatus.REVOKED
            self.store.save_permission(permission)
            revoked += 1

        for approval in self.store.list_approvals(pending_only=True):
            approval.status = ApprovalStatus.DECLINED
            approval.resolved_at = now()
            self.store.save_approval(approval)

        for helper in self.store.list_helpers():
            if helper.status == HelperStatus.WORKING:
                helper.status = HelperStatus.IDLE
                helper.current_task_id = ""
                self.store.save_helper(helper)

        self._stopped = True
        self.record(
            f"Emergency stop. Stopped {cancelled} task"
            f"{'s' if cancelled != 1 else ''} and removed {revoked} temporary "
            f"permission{'s' if revoked != 1 else ''}.",
            EventType.EMERGENCY_STOP)

        return {"stopped": True, "tasks_cancelled": cancelled,
                "permissions_revoked": revoked}

    def resume(self):
        """Allow new work again after an emergency stop."""
        self._stopped = False
        self.record("VAVE is accepting new work again.", EventType.NOTE)
        return {"stopped": False}

    @property
    def is_stopped(self):
        return self._stopped

    # -- summary ------------------------------------------------------------

    def status(self):
        """A small snapshot for a home screen or security page."""
        self.expire_permissions()
        self.sweep()
        return {
            "stopped": self._stopped,
            "devices": len(self.store.list_devices()),
            "helpers": len(self.store.list_helpers()),
            "agents_offline": sum(1 for helper in self.store.list_helpers()
                                  if helper.status == HelperStatus.OFFLINE),
            "agents_quarantined": sum(1 for helper in self.store.list_helpers()
                                      if helper.status == HelperStatus.QUARANTINED),
            "active_tasks": len(self.store.list_tasks(limit=500, active_only=True)),
            "pending_approvals": len(self.store.list_approvals(pending_only=True)),
            "temporary_access": len(self.store.list_permissions(active_only=True)),
        }


_control_plane = None
_singleton_lock = threading.Lock()


def get_control_plane(event_bus=None):
    """Shared instance used by the desktop app and the API."""
    global _control_plane
    with _singleton_lock:
        if _control_plane is None:
            _control_plane = ControlPlane(event_bus=event_bus)
        return _control_plane


def reset_control_plane():
    """Drop the shared instance. Used by tests."""
    global _control_plane
    with _singleton_lock:
        if _control_plane is not None:
            _control_plane.store.close()
        _control_plane = None
