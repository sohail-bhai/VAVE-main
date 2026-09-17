"""Regression tests for report.md / error_report.md — Reliability domain."""

import tempfile
import threading
import time
import types
import unittest
from pathlib import Path
from unittest import mock

from assistant.control.executor import CancelToken, TaskExecutor
from assistant.control.service import ControlPlane
from assistant.control.store import ControlStore


class Rel01ApprovalWaitTests(unittest.TestCase):
    """REL-01: a step that hits an approval must wait in its turn, not end.

    Before the fix, authorize() refused immediately, the model ended its
    turn, finish_step() marked the step done, and the later approval
    released a grant nobody was left to use.
    """

    def setUp(self):
        self.tempdir = tempfile.mkdtemp(prefix="vave-rel01-test-")
        self.store = ControlStore(Path(self.tempdir) / "control.db")
        self.plane = ControlPlane(store=self.store)

    def tearDown(self):
        self.store.close()
        import shutil
        shutil.rmtree(self.tempdir, ignore_errors=True)

    def executor(self, **kwargs):
        kwargs.setdefault("max_attempts", 1)
        kwargs.setdefault("approval_timeout", 10)
        return TaskExecutor(plane=self.plane, **kwargs)

    def _pending_approval(self):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            pending = self.plane.list_approvals(pending_only=True)
            if pending:
                return pending[0]
            time.sleep(0.05)
        self.fail("no approval was requested")

    def _ask_in_thread(self, authorize, tool="send_email"):
        outcome = {}

        def ask():
            outcome["result"] = authorize(tool)

        thread = threading.Thread(target=ask, daemon=True)
        thread.start()
        return thread, outcome

    def test_approval_mid_step_unblocks_the_tool(self):
        task = self.plane.create_task("Send the invoice")
        authorize = self.executor()._authorizer(task.id, None)
        thread, outcome = self._ask_in_thread(authorize)

        approval = self._pending_approval()
        self.plane.resolve_approval(approval.id, approved=True)
        thread.join(timeout=10)

        self.assertFalse(thread.is_alive())
        allowed, _reason = outcome["result"]
        self.assertTrue(allowed)
        self.assertTrue(
            self.plane.has_capability("google.gmail.send", task_id=task.id))

    def test_decline_refuses_and_cancels(self):
        from assistant.control.models import TaskStatus
        task = self.plane.create_task("Send the invoice")
        authorize = self.executor()._authorizer(task.id, None)
        thread, outcome = self._ask_in_thread(authorize)

        approval = self._pending_approval()
        self.plane.resolve_approval(approval.id, approved=False)
        thread.join(timeout=10)

        self.assertFalse(thread.is_alive())
        allowed, _reason = outcome["result"]
        self.assertFalse(allowed)
        self.assertEqual(TaskStatus.CANCELLED,
                         self.plane.get_task(task.id).status)

    def test_timeout_gives_up_and_cancels(self):
        task = self.plane.create_task("Send the invoice")
        authorize = self.executor(approval_timeout=0.3)._authorizer(
            task.id, None)
        started = time.monotonic()
        allowed, reason = authorize("send_email")
        elapsed = time.monotonic() - started

        self.assertFalse(allowed)
        self.assertIn("needs your approval", reason)
        self.assertLess(elapsed, 10)

    def test_cancelled_token_aborts_the_wait(self):
        task = self.plane.create_task("Send the invoice")
        token = CancelToken()
        authorize = self.executor()._authorizer(task.id, None, token=token)
        thread, outcome = self._ask_in_thread(authorize)

        # Let the wait start, then stop the task from elsewhere.
        time.sleep(0.3)
        token.cancel()
        thread.join(timeout=10)

        self.assertFalse(thread.is_alive())
        allowed, _reason = outcome["result"]
        self.assertFalse(allowed)


class Rel02BrowserThreadTests(unittest.TestCase):
    """REL-02: every Playwright call runs on one worker thread.

    A fake sync_api module stands in for Playwright so no browser launches.
    """

    def _fake_sync_api(self, idents):
        import sys

        class FakePage:
            url = "https://example.com/"

            def title(self):
                return "Example"

            def goto(self, url, wait_until=None):
                idents.append(("goto", threading.get_ident()))

        class FakeContext:
            def __init__(self):
                self.pages = [FakePage()]

            def set_default_timeout(self, ms):
                pass

        class FakeChromium:
            def launch_persistent_context(self, **kwargs):
                idents.append(("launch", threading.get_ident()))
                return FakeContext()

        class FakePW:
            def __init__(self):
                self.chromium = FakeChromium()

            def start(self):
                idents.append(("pw_start", threading.get_ident()))
                return self

        module = types.ModuleType("playwright.sync_api")
        module.sync_playwright = lambda: FakePW()
        return module

    def test_worker_thread_runs_nested_calls_inline(self):
        from assistant.browser.session import _BrowserThread
        worker = _BrowserThread()
        outer = worker.call(lambda: threading.get_ident())
        nested = worker.call(
            lambda: worker.call(lambda: threading.get_ident()))
        self.assertEqual(outer, nested)
        self.assertNotEqual(outer, threading.get_ident())

    def test_worker_propagates_errors_and_survives(self):
        from assistant.browser.session import _BrowserThread
        worker = _BrowserThread()

        def boom():
            raise ValueError("nope")

        with self.assertRaises(ValueError):
            worker.call(boom)
        self.assertEqual(7, worker.call(lambda: 7))

    def test_session_calls_from_any_thread_share_one_worker(self):
        import sys
        import tempfile
        from assistant.browser.session import BrowserSession
        idents = []
        fake = self._fake_sync_api(idents)
        tmpdir = tempfile.mkdtemp(prefix="vave-rel02-")
        self.addCleanup(__import__("shutil").rmtree, tmpdir,
                        ignore_errors=True)
        session = BrowserSession(profile_dir=tmpdir)

        with mock.patch.dict(sys.modules,
                               {"playwright.sync_api": fake}):
            session.goto("https://example.com")
            other = threading.Thread(
                target=lambda: session.goto("https://example.com"))
            other.start()
            other.join(timeout=30)
            session.close()

        kinds = {kind for kind, _ident in idents}
        self.assertIn("pw_start", kinds)
        self.assertIn("launch", kinds)
        self.assertIn("goto", kinds)
        workers = {ident for _kind, ident in idents}
        # One worker thread did everything; neither caller ran Playwright.
        self.assertEqual(1, len(workers))
        self.assertNotIn(threading.get_ident(), workers)


if __name__ == "__main__":
    unittest.main()
