"""Tests for the task executor: step execution, failures, stops and approvals."""

import shutil
import tempfile
import threading
import unittest
from pathlib import Path

from assistant.control.executor import TaskExecutor
from assistant.control.models import HelperStatus, StepStatus, TaskStatus
from assistant.control.service import ControlPlane
from assistant.control.store import ControlStore


class ExecutorTestCase(unittest.TestCase):
    """Each test gets its own database and a runner that never needs a model."""

    def setUp(self):
        self.tempdir = tempfile.mkdtemp(prefix="vave-test-")
        self.store = ControlStore(Path(self.tempdir) / "control.db")
        self.plane = ControlPlane(store=self.store)
        self.calls = []

    def tearDown(self):
        self.store.close()
        shutil.rmtree(self.tempdir, ignore_errors=True)

    def recording_runner(self, result="Done that."):
        def runner(instruction, context):
            self.calls.append((instruction, context))
            return result
        return runner

    def executor(self, runner=None, approval_timeout=5, max_attempts=1):
        # max_attempts=1 keeps these tests about the step lifecycle; retries
        # have their own tests in test_recovery.py.
        return TaskExecutor(plane=self.plane, runner=runner or self.recording_runner(),
                            approval_timeout=approval_timeout,
                            max_attempts=max_attempts)


class StepExecutionTests(ExecutorTestCase):
    def test_runs_every_step_in_order_and_completes_the_task(self):
        task = self.plane.create_task("Tidy my notes",
                                      steps=["Reading notes", "Rewriting notes"])

        self.executor().run(task.id)

        self.assertEqual(["Reading notes", "Rewriting notes"],
                         [instruction for instruction, _ in self.calls])
        finished = self.plane.get_task(task.id)
        self.assertEqual(TaskStatus.COMPLETED, finished.status)
        self.assertEqual(
            [StepStatus.DONE, StepStatus.DONE],
            [step.status for step in self.store.list_steps(task.id)])

    def test_step_outcome_is_recorded_on_the_step(self):
        task = self.plane.create_task("Look something up", steps=["Searching"])

        self.executor(self.recording_runner("Found 3 results.")).run(task.id)

        step = self.store.list_steps(task.id)[0]
        self.assertEqual("Found 3 results.", step.detail)

    def test_later_steps_see_what_earlier_steps_produced(self):
        task = self.plane.create_task("Two parts",
                                      steps=["First part", "Second part"])

        self.executor(self.recording_runner("An outcome.")).run(task.id)

        _, second_context = self.calls[1]
        self.assertIn("First part", second_context)
        self.assertIn("An outcome.", second_context)

    def test_first_step_gets_no_context(self):
        task = self.plane.create_task("One part", steps=["Only part"])

        self.executor().run(task.id)

        self.assertEqual("", self.calls[0][1])

    def test_a_goal_without_steps_is_run_as_one_piece_of_work(self):
        task = self.plane.create_task("Just do the thing")

        self.executor().run(task.id)

        self.assertEqual(["Just do the thing"],
                         [instruction for instruction, _ in self.calls])
        self.assertEqual(TaskStatus.COMPLETED, self.plane.get_task(task.id).status)

    def test_progress_is_checkpointed_after_each_step(self):
        task = self.plane.create_task("Two parts",
                                      steps=["First part", "Second part"])

        self.executor().run(task.id)

        checkpoint = self.plane.get_task(task.id).checkpoint
        self.assertEqual([0, 1], checkpoint["completed"])
        self.assertEqual(2, len(checkpoint["outcomes"]))

    def test_agent_is_marked_working_during_execution_and_idle_after(self):
        helper = self.plane.register_helper("Researcher", ["research"])
        task = self.plane.create_task("Investigate", steps=["Search papers"], capability="research")

        observed_statuses = []

        def runner(instruction, context):
            current = self.plane.get_helper(helper.id)
            observed_statuses.append((current.status, current.current_task_id))
            return "Found paper."

        self.executor(runner).run(task.id)

        self.assertEqual([(HelperStatus.WORKING, task.id)], observed_statuses)
        after = self.plane.get_helper(helper.id)
        self.assertEqual(HelperStatus.IDLE, after.status)
        self.assertEqual("", after.current_task_id)

    def test_already_finished_steps_are_not_repeated(self):
        task = self.plane.create_task("Two parts",
                                      steps=["First part", "Second part"])
        self.plane.start_step(task.id, 0)
        self.plane.finish_step(task.id, 0, "Already handled.")

        self.executor().run(task.id)

        self.assertEqual(["Second part"],
                         [instruction for instruction, _ in self.calls])

    def test_activity_reports_each_step(self):
        task = self.plane.create_task("Tidy my notes", steps=["Reading notes"])

        self.executor(self.recording_runner("Read 12 notes.")).run(task.id)

        messages = [event.message for event in self.plane.list_events(task_id=task.id)]
        self.assertIn("Reading notes", messages)
        self.assertIn("Read 12 notes.", messages)


class FailureTests(ExecutorTestCase):
    def failing_runner(self, error):
        def runner(instruction, context):
            self.calls.append((instruction, context))
            raise error
        return runner

    def test_a_failing_step_fails_the_task_with_the_real_reason(self):
        task = self.plane.create_task("Two parts",
                                      steps=["First part", "Second part"])

        self.executor(self.failing_runner(RuntimeError("Ollama is not running"))).run(task.id)

        finished = self.plane.get_task(task.id)
        self.assertEqual(TaskStatus.FAILED, finished.status)
        self.assertIn("Ollama is not running", finished.summary)

    def test_a_failing_step_stops_the_remaining_steps(self):
        task = self.plane.create_task("Two parts",
                                      steps=["First part", "Second part"])

        self.executor(self.failing_runner(RuntimeError("nope"))).run(task.id)

        self.assertEqual(1, len(self.calls))
        steps = self.store.list_steps(task.id)
        self.assertEqual(StepStatus.FAILED, steps[0].status)
        self.assertEqual(StepStatus.PENDING, steps[1].status)

    def test_a_crashing_background_run_fails_the_task_rather_than_hanging(self):
        task = self.plane.create_task("Two parts", steps=["First part"])
        executor = self.executor(self.failing_runner(RuntimeError("boom")))

        executor.submit(task.id)
        executor.wait(task.id, timeout=5)

        self.assertEqual(TaskStatus.FAILED, self.plane.get_task(task.id).status)


class InterruptionTests(ExecutorTestCase):
    def test_an_emergency_stop_cancels_a_task_before_it_starts(self):
        task = self.plane.create_task("Tidy my notes", steps=["Reading notes"])
        self.plane.emergency_stop()

        self.executor().run(task.id)

        self.assertEqual([], self.calls)
        self.assertEqual(TaskStatus.CANCELLED, self.plane.get_task(task.id).status)

    def test_a_stop_between_steps_leaves_the_rest_undone(self):
        task = self.plane.create_task("Two parts",
                                      steps=["First part", "Second part"])

        def runner(instruction, context):
            self.calls.append((instruction, context))
            self.plane.emergency_stop()
            return "Did it."

        self.executor(runner).run(task.id)

        self.assertEqual(1, len(self.calls))
        self.assertEqual(TaskStatus.CANCELLED, self.plane.get_task(task.id).status)

    def test_a_cancelled_task_is_not_worked_further(self):
        task = self.plane.create_task("Two parts",
                                      steps=["First part", "Second part"])

        def runner(instruction, context):
            self.calls.append((instruction, context))
            self.plane.cancel_task(task.id)
            return "Did it."

        self.executor(runner).run(task.id)

        self.assertEqual(1, len(self.calls))
        self.assertEqual(TaskStatus.CANCELLED, self.plane.get_task(task.id).status)

    def test_a_finished_task_is_never_rerun(self):
        task = self.plane.create_task("Tidy my notes", steps=["Reading notes"])
        self.plane.complete_task(task.id, "Already done.")

        self.executor().run(task.id)

        self.assertEqual([], self.calls)

    def test_an_unknown_task_is_reported_as_missing(self):
        self.assertIsNone(self.executor().run("no-such-task"))


class ApprovalTests(ExecutorTestCase):
    def test_work_pauses_until_the_user_approves(self):
        task = self.plane.create_task("Two parts",
                                      steps=["First part", "Second part"])
        approval = self.plane.request_approval(
            action="Merge changes", question="Shall I merge?", task_id=task.id)

        executor = self.executor()
        executor.submit(task.id)

        # Nothing runs while the decision is outstanding.
        threading.Event().wait(0.3)
        self.assertEqual([], self.calls)

        self.plane.resolve_approval(approval.id, approved=True)
        executor.wait(task.id, timeout=5)

        self.assertEqual(2, len(self.calls))
        self.assertEqual(TaskStatus.COMPLETED, self.plane.get_task(task.id).status)

    def test_declining_an_approval_stops_the_task(self):
        task = self.plane.create_task("Two parts", steps=["First part"])
        approval = self.plane.request_approval(
            action="Merge changes", question="Shall I merge?", task_id=task.id)

        executor = self.executor()
        executor.submit(task.id)
        self.plane.resolve_approval(approval.id, approved=False)
        executor.wait(task.id, timeout=5)

        self.assertEqual([], self.calls)
        self.assertEqual(TaskStatus.CANCELLED, self.plane.get_task(task.id).status)

    def test_an_unanswered_approval_times_out_instead_of_waiting_forever(self):
        task = self.plane.create_task("Two parts", steps=["First part"])
        self.plane.request_approval(
            action="Merge changes", question="Shall I merge?", task_id=task.id)

        self.executor(approval_timeout=0.2).run(task.id)

        finished = self.plane.get_task(task.id)
        self.assertEqual(TaskStatus.CANCELLED, finished.status)
        self.assertEqual([], self.calls)


class SubmissionTests(ExecutorTestCase):
    def test_start_creates_the_task_and_works_it(self):
        executor = self.executor()

        task = executor.start("Tidy my notes", steps=["Reading notes"])
        executor.wait(task.id, timeout=5)

        self.assertEqual(["Reading notes"],
                         [instruction for instruction, _ in self.calls])
        self.assertEqual(TaskStatus.COMPLETED, self.plane.get_task(task.id).status)

    def test_submitting_twice_does_not_run_the_work_twice(self):
        release = threading.Event()

        def runner(instruction, context):
            self.calls.append((instruction, context))
            release.wait(2)
            return "Did it."

        executor = self.executor(runner)
        task = self.plane.create_task("Tidy my notes", steps=["Reading notes"])

        executor.submit(task.id)
        executor.submit(task.id)
        release.set()
        executor.wait(task.id, timeout=5)

        self.assertEqual(1, len(self.calls))

    def test_submitting_an_unknown_task_returns_nothing(self):
        self.assertIsNone(self.executor().submit("no-such-task"))


if __name__ == "__main__":
    unittest.main()
