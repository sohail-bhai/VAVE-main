"""Tests for retries, resuming interrupted work, and handing a step to another agent.

The promise here is that a task survives a bad moment: a transient failure is
retried, work already done is never redone, and a step can be passed to an
agent better suited to it without starting a second task.
"""

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from assistant.control.adapters import AdapterRegistry, HttpAdapter, NativeAdapter
from assistant.control.executor import TaskExecutor
from assistant.control.models import StepResult, StepStatus, TaskStatus
from assistant.control.service import ControlPlane
from assistant.control.store import ControlStore


class RecoveryTestCase(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.mkdtemp(prefix="vave-recovery-test-")
        self.path = Path(self.tempdir) / "control.db"
        self.store = ControlStore(self.path)
        self.plane = ControlPlane(store=self.store)
        self.calls = []

    def tearDown(self):
        self.store.close()
        shutil.rmtree(self.tempdir, ignore_errors=True)

    def executor(self, runner=None, **kwargs):
        kwargs.setdefault("backoff", 0)
        return TaskExecutor(plane=self.plane,
                            runner=runner or (lambda instruction, context: "Done."),
                            approval_timeout=5, **kwargs)


class StepResultTests(unittest.TestCase):
    def test_plain_text_becomes_a_successful_result(self):
        result = StepResult.of("Found 3 files.")

        self.assertTrue(result.ok)
        self.assertEqual("Found 3 files.", result.output)

    def test_empty_text_still_says_something(self):
        self.assertEqual("Done.", StepResult.of("").output)

    def test_a_dict_carries_artifacts_and_failure(self):
        result = StepResult.of({"ok": False, "error": "no network",
                                "artifacts": ["/tmp/report.md"]})

        self.assertFalse(result.ok)
        self.assertEqual("no network", result.error)
        self.assertEqual(["/tmp/report.md"], result.artifacts)

    def test_a_result_passes_through_unchanged(self):
        original = StepResult(output="Done.")

        self.assertIs(original, StepResult.of(original))


class RetryTests(RecoveryTestCase):
    def flaky(self, failures, error=None):
        def run(instruction, context):
            self.calls.append(instruction)
            if len(self.calls) <= failures:
                raise (error or RuntimeError("the model was busy"))
            return "Done at last."
        return run

    def test_a_transient_failure_is_retried(self):
        task = self.plane.create_task("One part", steps=["Searching"])

        self.executor(self.flaky(1)).run(task.id)

        self.assertEqual(2, len(self.calls))
        self.assertEqual(TaskStatus.COMPLETED, self.plane.get_task(task.id).status)

    def test_retries_stop_at_the_cap(self):
        task = self.plane.create_task("One part", steps=["Searching"])

        self.executor(self.flaky(10), max_attempts=3).run(task.id)

        self.assertEqual(3, len(self.calls))
        self.assertEqual(TaskStatus.FAILED, self.plane.get_task(task.id).status)

    def test_the_real_reason_survives_the_retries(self):
        task = self.plane.create_task("One part", steps=["Searching"])

        self.executor(self.flaky(10, RuntimeError("Ollama is not running")),
                      max_attempts=2).run(task.id)

        self.assertIn("Ollama is not running", self.plane.get_task(task.id).summary)

    def test_attempts_are_counted_on_the_step(self):
        task = self.plane.create_task("One part", steps=["Searching"])

        self.executor(self.flaky(1)).run(task.id)

        self.assertEqual(2, self.store.list_steps(task.id)[0].attempts)

    def test_a_retry_is_visible_on_the_timeline(self):
        task = self.plane.create_task("One part", steps=["Searching"])

        self.executor(self.flaky(1)).run(task.id)

        messages = [event.message for event in self.plane.list_events(task_id=task.id)]
        self.assertIn("That didn't work. Trying 'Searching' again.", messages)

    def test_an_agent_that_says_it_failed_is_retried_too(self):
        results = [StepResult(ok=False, error="page did not load"),
                   StepResult(output="Got it.")]

        def run(instruction, context):
            self.calls.append(instruction)
            return results[len(self.calls) - 1]

        task = self.plane.create_task("One part", steps=["Loading the page"])
        self.executor(run).run(task.id)

        self.assertEqual(2, len(self.calls))
        self.assertEqual(TaskStatus.COMPLETED, self.plane.get_task(task.id).status)

    def test_artifacts_are_recorded_on_the_step(self):
        def run(instruction, context):
            return StepResult(output="Wrote the report.",
                              artifacts=["/tmp/report.md"])

        task = self.plane.create_task("One part", steps=["Writing"])
        self.executor(run).run(task.id)

        self.assertEqual(["/tmp/report.md"], self.store.list_steps(task.id)[0].artifacts)

    def test_a_cancelled_step_is_never_retried(self):
        def run(instruction, context):
            self.calls.append(instruction)
            executor.stop(task.id)
            executor.cancel_token(task.id).check()

        task = self.plane.create_task("One part", steps=["Searching"])
        executor = self.executor(run)

        executor.run(task.id)

        self.assertEqual(1, len(self.calls))


class ResumeTests(RecoveryTestCase):
    def interrupted_task(self):
        """A task left mid-flight, as a killed process would leave it."""
        task = self.plane.create_task("Two parts", steps=["First", "Second"])
        self.plane.start_step(task.id, 0)
        self.plane.finish_step(task.id, 0, "Already done.")
        return task

    def test_finished_steps_are_never_redone(self):
        task = self.interrupted_task()

        def run(instruction, context):
            self.calls.append(instruction)
            return "Done."

        self.executor(run).run(task.id)

        self.assertEqual(["Second"], self.calls)

    def test_an_interrupted_task_is_picked_back_up(self):
        task = self.interrupted_task()

        def run(instruction, context):
            self.calls.append(instruction)
            return "Done."

        executor = self.executor(run)
        resumed = executor.resume_interrupted()
        executor.wait(task.id, timeout=5)

        self.assertEqual([task.id], [item.id for item in resumed])
        self.assertEqual(["Second"], self.calls)
        self.assertEqual(TaskStatus.COMPLETED, self.plane.get_task(task.id).status)

    def test_resuming_says_so_on_the_timeline(self):
        task = self.interrupted_task()
        executor = self.executor()

        executor.resume_interrupted()
        executor.wait(task.id, timeout=5)

        messages = [event.message for event in self.plane.list_events(task_id=task.id)]
        self.assertIn("Picking this back up where it stopped.", messages)

    def test_finished_tasks_are_left_alone(self):
        task = self.plane.create_task("One part", steps=["First"])
        self.plane.complete_task(task.id, "Done.")

        self.assertEqual([], self.executor().resume_interrupted())

    def test_a_task_with_no_steps_is_not_resumed(self):
        self.plane.create_task("Just a goal")

        self.assertEqual([], self.executor().resume_interrupted())

    def test_progress_survives_a_restart(self):
        task = self.interrupted_task()
        self.store.close()

        store = ControlStore(self.path)
        self.addCleanup(store.close)
        plane = ControlPlane(store=store)

        steps = store.list_steps(task.id)
        self.assertEqual(StepStatus.DONE, steps[0].status)
        self.assertEqual("Already done.", steps[0].detail)
        self.assertEqual(1, len(plane.interrupted_tasks()))


    def test_waiting_approval_tasks_are_picked_back_up(self):
        # A restart must not strand work that was waiting on the user.
        task = self.plane.create_task("Needs a yes", steps=["First"])
        self.plane.request_approval(action="Do it", question="May I?",
                                    task_id=task.id)

        ids = [item.id for item in self.plane.interrupted_tasks()]
        self.assertIn(task.id, ids)

        executor = self.executor()
        with mock.patch.object(executor, "submit",
                               return_value=task) as mock_submit:
            resumed = executor.resume_interrupted()
        self.assertEqual([task.id], [item.id for item in resumed])
        mock_submit.assert_called_once_with(task.id)

    def test_later_steps_see_checkpointed_outputs(self):
        task = self.interrupted_task()
        self.plane.save_checkpoint(task.id, {
            "completed": [0],
            "outcomes": [{"step": "First", "outcome": "Already done."}],
        })

        seen = []

        def run(instruction, context):
            seen.append((instruction, context))
            return "Done."

        self.executor(run).run(task.id)

        self.assertEqual(1, len(seen))
        instruction, context = seen[0]
        self.assertEqual("Second", instruction)
        self.assertIn("Already done.", context)


class DelegationTests(RecoveryTestCase):
    def test_a_step_can_be_handed_to_an_agent_by_capability(self):
        agent = self.plane.register_helper("Document agent", ["documents"])
        task = self.plane.create_task("Find and read the report", steps=["Finding it"])

        step = self.plane.delegate(task.id, "Extracting the payment date",
                                   capability="documents")

        self.assertEqual(agent.id, step.agent_id)
        self.assertEqual(1, step.position)

    def test_delegated_work_waits_for_what_is_unfinished(self):
        self.plane.register_helper("Document agent", ["documents"])
        task = self.plane.create_task("Two parts", steps=["First", "Second"])

        step = self.plane.delegate(task.id, "Extracting the date",
                                   capability="documents")

        self.assertEqual([0, 1], step.depends_on)

    def test_delegation_can_name_its_own_dependencies(self):
        agent = self.plane.register_helper("Document agent", ["documents"])
        task = self.plane.create_task("Two parts", steps=["First", "Second"])

        step = self.plane.delegate(task.id, "Extracting the date",
                                   agent_id=agent.id, after=[0])

        self.assertEqual([0], step.depends_on)

    def test_delegation_without_anyone_available_is_refused(self):
        task = self.plane.create_task("One part", steps=["First"])

        self.assertIsNone(self.plane.delegate(task.id, "Extracting the date",
                                              capability="documents"))

    def test_a_delegated_step_runs_in_the_same_task(self):
        self.plane.register_helper("Document agent", ["documents"])
        task = self.plane.create_task("Find and read the report", steps=["Finding it"])

        def run(instruction, context):
            self.calls.append(instruction)
            if instruction == "Finding it":
                self.plane.delegate(task.id, "Extracting the date",
                                    capability="documents")
            return "Done."

        self.executor(run).run(task.id)

        self.assertEqual(["Finding it", "Extracting the date"], self.calls)
        self.assertEqual(TaskStatus.COMPLETED, self.plane.get_task(task.id).status)

    def test_the_handoff_is_announced(self):
        self.plane.register_helper("Document agent", ["documents"])
        task = self.plane.create_task("One part", steps=["First"])

        self.plane.delegate(task.id, "Extracting the date", capability="documents")

        messages = [event.message for event in self.plane.list_events(task_id=task.id)]
        self.assertIn("Asked Document agent to extracting the date.", messages)

    def test_a_delegated_step_runs_through_its_agents_adapter(self):
        agent = self.plane.register_helper("Remote agent", ["documents"],
                                           framework="http",
                                           endpoint="http://localhost:9000/run")
        task = self.plane.create_task("One part", steps=["First"])
        self.plane.delegate(task.id, "Extracting the date", agent_id=agent.id)

        registry = AdapterRegistry(default=NativeAdapter(runner=lambda i, c: "local"))
        registry.register(HttpAdapter(transport=lambda *args: {"output": "remote"}))
        TaskExecutor(plane=self.plane, adapters=registry, backoff=0).run(task.id)

        details = [step.detail for step in self.store.list_steps(task.id)]
        self.assertEqual(["local", "remote"], details)


if __name__ == "__main__":
    unittest.main()
