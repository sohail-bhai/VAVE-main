"""Executes control plane tasks by running their steps through the AI brain.

The control plane records what should happen; this is the piece that makes it
happen. It walks a task's steps in order, hands each one to the VAVE agent
loop, and writes the outcome back so every client - desktop, mobile or the
activity stream - sees real progress rather than a plan nobody is working on.

The runner is injectable, so the coordination logic here is testable without a
local model, and so a future remote helper can be plugged in without touching
the control plane.
"""

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from assistant.control.adapters import AdapterRegistry, NativeAdapter
from assistant.control.capabilities import capability_for_tool
from assistant.control.models import StepResult, StepStatus, TaskStatus
from assistant.control.planner import Planner
from assistant.control.service import get_control_plane

logger = logging.getLogger(__name__)

# How long a step may wait on a user decision before we give up on it.
DEFAULT_APPROVAL_TIMEOUT = 15 * 60

# How often we look for a decision or a stop while waiting.
POLL_SECONDS = 0.1

# Steps carry their predecessors' outcomes forward, but not without limit.
MAX_CONTEXT_CHARS = 2000

# How many independent steps may run at once. Small on purpose: these steps
# drive one computer, and a local model is the bottleneck anyway.
MAX_PARALLEL_STEPS = 3

# A step that fails is tried again: most failures here are a busy model or a
# flaky network, not a wrong plan.
MAX_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = 0.5


class Cancelled(Exception):
    """The work was stopped part-way, by the user or by an emergency stop."""


class CancelToken:
    """A flag a running step checks so it can stop between tool calls."""

    def __init__(self):
        self._event = threading.Event()

    def cancel(self):
        self._event.set()

    @property
    def cancelled(self):
        return self._event.is_set()

    def check(self):
        if self._event.is_set():
            raise Cancelled("Stopped part-way.")

    def __bool__(self):
        return not self._event.is_set()


class TaskExecutor:
    """Runs tasks step by step and keeps the control plane honest about it."""

    def __init__(self, plane=None, runner=None,
                 approval_timeout=DEFAULT_APPROVAL_TIMEOUT, adapters=None,
                 planner=None, max_parallel=MAX_PARALLEL_STEPS,
                 max_attempts=MAX_ATTEMPTS, backoff=RETRY_BACKOFF_SECONDS):
        self.plane = plane or get_control_plane()
        self._runner = runner
        self.adapters = adapters or AdapterRegistry(
            default=NativeAdapter(runner=runner))
        self.planner = planner or Planner()
        self.max_parallel = max(1, max_parallel)
        self.max_attempts = max(1, max_attempts)
        self.backoff = backoff
        self.approval_timeout = approval_timeout
        self._threads = {}
        self._tokens = {}
        self._lock = threading.RLock()

    # -- running ------------------------------------------------------------

    def submit(self, task_id):
        """Start a task in the background. Returns the task, or None if unknown."""
        task = self.plane.get_task(task_id)
        if task is None:
            return None

        with self._lock:
            existing = self._threads.get(task_id)
            if existing is not None and existing.is_alive():
                return task

            thread = threading.Thread(target=self._run_quietly, args=(task_id,),
                                      name=f"task-{task_id[:8]}", daemon=True)
            self._threads[task_id] = thread

        thread.start()
        return task

    def start(self, goal, steps=None, capability=None, plan=None):
        """Create a task and immediately begin working it.

        With no steps given, the planner turns the goal into steps first, so a
        caller can hand over a sentence and still get observable progress.
        """
        if steps is None and (plan or (plan is None and self.planner is not None)):
            steps = self.planner.plan(goal)

        task = self.plane.create_task(goal, steps=steps, capability=capability)
        self.submit(task.id)
        return task

    def resume_interrupted(self):
        """Pick up tasks that were running when the process last stopped.

        Finished steps are never redone: the graph already records what was
        done, so resuming starts at the first step that is not.
        """
        resumed = []
        for task in self.plane.interrupted_tasks():
            steps = self.plane.store.list_steps(task.id)
            if not steps or all(step.is_finished for step in steps):
                continue
            self.plane.record("Picking this back up where it stopped.",
                              task_id=task.id)
            self.submit(task.id)
            resumed.append(task)
        return resumed

    def stop(self, task_id, reason="You stopped this."):
        """Stop a running task now, without waiting for the current step."""
        with self._lock:
            token = self._tokens.get(task_id)
        if token is not None:
            token.cancel()
        return self.plane.cancel_task(task_id, reason)

    def wait(self, task_id, timeout=60):
        """Block until a submitted task's thread finishes. Returns the task."""
        with self._lock:
            thread = self._threads.get(task_id)
        if thread is not None:
            thread.join(timeout)
        return self.plane.get_task(task_id)

    def _run_quietly(self, task_id):
        try:
            self.run(task_id)
        except Exception:
            # A background thread must not die silently, and it must not take
            # the process with it either.
            logger.exception("Task %s crashed", task_id)
            self.plane.fail_task(task_id, "Something went wrong while working on this.")

    def run(self, task_id):
        """Work a task to completion on the calling thread.

        Steps form a graph: everything whose dependencies are done runs, up to
        `max_parallel` at a time. Every exit path is recorded - completion with
        a summary, an honest failure with the real reason, or cancellation when
        the user or an emergency stop says so.
        """
        task = self.plane.get_task(task_id)
        if task is None:
            return None
        if task.status.is_terminal:
            return task

        if self.plane.is_stopped:
            return self.plane.cancel_task(task_id, "VAVE is stopped.")

        token = CancelToken()
        with self._lock:
            self._tokens[task_id] = token

        try:
            steps = self.plane.store.list_steps(task_id)
            if not steps:
                # A goal with no plan is still work: treat the goal as the step.
                return self._run_single(task, token)
            return self._run_graph(task, steps, token)
        finally:
            with self._lock:
                self._tokens.pop(task_id, None)

    def _run_graph(self, task, steps, token):
        """Run the step graph, widest first, until it finishes or stops."""
        task_id = task.id
        outcomes = []
        done = {step.position for step in steps if step.is_finished}
        remaining = [step for step in steps if not step.is_finished]

        with ThreadPoolExecutor(max_workers=self.max_parallel,
                                thread_name_prefix=f"step-{task_id[:6]}") as pool:
            while remaining:
                halted = self._halt_reason(task_id)
                if halted:
                    token.cancel()
                    return self.plane.cancel_task(task_id, halted)

                if not self._wait_for_approvals(task_id):
                    return self.plane.get_task(task_id)

                ready = [step for step in remaining
                         if all(position in done for position in step.depends_on)]
                if not ready:
                    # Every remaining step waits on something that never ran.
                    return self.plane.fail_task(
                        task_id, "These steps depend on work that never finished.")

                batch = ready[:self.max_parallel]
                results = list(pool.map(
                    lambda step: self._run_step(task_id, step, outcomes, token),
                    batch))

                for step, (ok, outcome) in zip(batch, results):
                    if not ok:
                        token.cancel()
                        if isinstance(outcome, Cancelled):
                            return self.plane.get_task(task_id)
                        return self.plane.fail_task(
                            task_id, f"Stopped at '{step.label}'. {outcome}")

                    done.add(step.position)
                    outcomes.append((step.label, outcome))

                # Re-read the graph: a step may have delegated work into it.
                remaining = [step for step in self.plane.store.list_steps(task_id)
                             if not step.is_finished and step.position not in done]
                self.plane.save_checkpoint(task_id, {
                    "completed": sorted(done),
                    "outcomes": [{"step": label, "outcome": text}
                                 for label, text in outcomes],
                })

        return self.plane.complete_task(task_id, outcomes[-1][1] if outcomes else "Done.")

    def _run_step(self, task_id, step, outcomes, token):
        """Run one step, retrying a failure. Returns (ok, outcome or error).

        Exceptions are returned rather than raised so one failing step cannot
        take the thread pool, or the rest of the task's bookkeeping, with it.
        """
        agent = self._agent_for(task_id, step)
        self.plane.start_step(task_id, step.position)
        if agent is not None:
            self.plane.set_helper_working(agent.id, task_id)
        last_error = None

        try:
            for attempt in range(1, self.max_attempts + 1):
                started = time.monotonic()
                self.plane.record_attempt(task_id, step.position)

                try:
                    token.check()
                    result = StepResult.of(self._call_runner(
                        step.label, self._context(outcomes), agent=agent,
                        token=token, task_id=task_id))
                except Cancelled as stopped:
                    self.plane.finish_step(task_id, step.position,
                                           detail="Stopped part-way.", failed=True)
                    return False, stopped
                except Exception as error:
                    logger.warning("Step %s of task %s failed on attempt %s: %s",
                                   step.position, task_id, attempt, error)
                    self._report_health(agent, started, ok=False)
                    last_error = error
                    if attempt < self.max_attempts and not token.cancelled:
                        self.plane.record(
                            f"That didn't work. Trying '{step.label}' again.",
                            task_id=task_id)
                        time.sleep(self.backoff * (2 ** (attempt - 1)))
                        continue
                    break

                if not result.ok:
                    # The agent answered, but said it could not do the work.
                    last_error = RuntimeError(result.error or "The agent could not do this.")
                    self._report_health(agent, started, ok=False)
                    if attempt < self.max_attempts and not token.cancelled:
                        time.sleep(self.backoff * (2 ** (attempt - 1)))
                        continue
                    break

                self._report_health(agent, started, ok=True)
                self.plane.finish_step(task_id, step.position, detail=result.output,
                                       artifacts=result.artifacts)
                return True, result.output

            self.plane.finish_step(task_id, step.position,
                                   detail=f"Couldn't do this: {last_error}", failed=True)
            return False, last_error
        finally:
            if agent is not None:
                self.plane.set_helper_idle(agent.id)

    def _agent_for(self, task_id, step):
        """The agent that should do this step: the step's own, or the task's."""
        if step.agent_id:
            return self.plane.get_helper(step.agent_id)
        task = self.plane.get_task(task_id)
        if task is not None and task.helper_id:
            return self.plane.get_helper(task.helper_id)
        return None

    def _run_single(self, task, token):
        """Work a task whose goal was never broken into steps."""
        agent = self.plane.get_helper(task.helper_id) if task.helper_id else None
        if agent is not None:
            self.plane.set_helper_working(agent.id, task.id)
        started = time.monotonic()
        try:
            try:
                outcome = self._call_runner(task.goal, "", agent=agent, token=token,
                                            task_id=task.id)
            except Cancelled:
                return self.plane.cancel_task(task.id, "Stopped part-way.")
            except Exception as error:
                logger.exception("Task %s failed", task.id)
                self._report_health(agent, started, ok=False)
                return self.plane.fail_task(task.id, str(error))

            self._report_health(agent, started, ok=True)
            return self.plane.complete_task(task.id, outcome)
        finally:
            if agent is not None:
                self.plane.set_helper_idle(agent.id)

    # -- collaborators ------------------------------------------------------

    def _call_runner(self, instruction, context, agent=None, token=None,
                     task_id=""):
        """Hand one step to whichever adapter can drive this agent."""
        adapter = self.adapters.for_agent(agent)

        try:
            return adapter.run_step(instruction, context=context, agent=agent,
                                    token=token,
                                    authorize=self._authorizer(task_id, agent),
                                    resolve_secrets=self.plane.secrets.resolve)
        except TypeError:
            # An adapter that predates cancellation still works, it just
            # cannot be interrupted part-way.
            return adapter.run_step(instruction, context=context, agent=agent)

    def _authorizer(self, task_id, agent):
        """Decides, per tool, whether the running step may use it.

        This is where a brokered capability stops being advice: a tool whose
        capability was never granted is refused at the moment it is called.
        """
        agent_id = agent.id if agent is not None else ""

        def authorize(tool_name):
            capability = capability_for_tool(tool_name)
            if not capability:
                return True, ""

            if self.plane.has_capability(capability, task_id=task_id):
                return True, ""

            result = self.plane.request_capability(
                capability, agent_id=agent_id, task_id=task_id,
                reason="A running step needs this.")

            if result["status"] == "granted":
                return True, ""
            if result["status"] == "waiting":
                return False, (f"{capability} needs your approval first. "
                               "Ask again once it is approved.")
            return False, f"{capability} is not allowed. {result['judgement']['reason']}"

        return authorize

    def _report_health(self, agent, started, ok):
        """Health comes from work that actually ran, not from guesses."""
        if agent is None:
            return
        latency_ms = int((time.monotonic() - started) * 1000)
        self.plane.heartbeat(agent.id, latency_ms=latency_ms, ok=ok)

    def _context(self, outcomes):
        """What earlier steps produced, trimmed so the model's context survives."""
        if not outcomes:
            return ""
        text = "\n".join(f"- {label}: {outcome}" for label, outcome in outcomes)
        if len(text) > MAX_CONTEXT_CHARS:
            text = "..." + text[-MAX_CONTEXT_CHARS:]
        return text

    # -- interruptions ------------------------------------------------------

    def cancel_token(self, task_id):
        """The token for a running task, if one is running."""
        with self._lock:
            return self._tokens.get(task_id)

    def _halt_reason(self, task_id):
        """Why this task should stop now, or an empty string to keep going."""
        if self.plane.is_stopped:
            return "VAVE is stopped."

        task = self.plane.get_task(task_id)
        if task is None:
            return "This task no longer exists."
        if task.status == TaskStatus.CANCELLED:
            return "You stopped this."
        if task.status.is_terminal:
            return "This task already finished."
        return ""

    def _wait_for_approvals(self, task_id):
        """Hold while the user decides. False means the task must not continue.

        The control plane already moved the task to `waiting_approval` when the
        approval was requested, so this only has to respect that decision.
        """
        deadline = time.monotonic() + self.approval_timeout

        while True:
            task = self.plane.get_task(task_id)
            if task is None or task.status.is_terminal:
                return False
            if self.plane.is_stopped:
                self.plane.cancel_task(task_id, "VAVE is stopped.")
                return False
            if task.status != TaskStatus.WAITING_APPROVAL:
                return True

            if time.monotonic() >= deadline:
                self.plane.cancel_task(
                    task_id, "Nobody answered the approval request, so this stopped.")
                return False

            time.sleep(POLL_SECONDS)


_executor = None
_executor_lock = threading.Lock()


def get_executor(plane=None, runner=None):
    """Shared executor used by the desktop app and the API."""
    global _executor
    with _executor_lock:
        if _executor is None:
            _executor = TaskExecutor(plane=plane, runner=runner)
        return _executor


def reset_executor():
    """Drop the shared executor. Used by tests."""
    global _executor
    with _executor_lock:
        _executor = None
