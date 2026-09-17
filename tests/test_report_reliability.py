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


class Rel01CloseAppGuardTests(unittest.TestCase):
    """N-REL-01: empty/short names must never reach process matching."""

    def _close(self, name, processes):
        from assistant import system_tasks
        with mock.patch("psutil.process_iter",
                        return_value=processes) as mock_iter, \
             mock.patch("assistant.system_tasks.close_window",
                        return_value="No window."), \
             mock.patch("assistant.system_tasks.speak"):
            result = system_tasks.close_app(name)
        return result, mock_iter

    def _proc(self, name):
        proc = mock.MagicMock()
        proc.info = {"name": name, "pid": 4242}
        return proc

    def test_empty_and_short_names_are_refused_up_front(self):
        for name in ("", "close", "kill", "s", "it", "  "):
            with self.subTest(name=name):
                svchost = self._proc("svchost.exe")
                result, mock_iter = self._close(name, [svchost])
                self.assertIn("which app", result)
                mock_iter.assert_not_called()
                svchost.terminate.assert_not_called()

    def test_exact_match_still_terminates(self):
        proc = self._proc("notepad.exe")
        result, _mock_iter = self._close("notepad", [proc])
        proc.terminate.assert_called_once()
        self.assertIn("Closed", result)

    def test_near_match_does_not_terminate(self):
        proc = self._proc("sihost.exe")
        result, _mock_iter = self._close("host", [proc])
        proc.terminate.assert_not_called()
        self.assertIn("No running process", result)


class Rel02MultiApprovalTests(unittest.TestCase):
    """N-REL-02: one approval must not resume a task with others pending."""

    def setUp(self):
        import tempfile
        from pathlib import Path
        from assistant.control.service import ControlPlane
        from assistant.control.store import ControlStore
        self.tempdir = tempfile.mkdtemp(prefix="vave-rel02-test-")
        self.store = ControlStore(Path(self.tempdir) / "control.db")
        self.plane = ControlPlane(store=self.store)

    def tearDown(self):
        self.store.close()
        import shutil
        shutil.rmtree(self.tempdir, ignore_errors=True)

    def _two_pending(self, task_id):
        first = self.plane.request_approval(
            action="Do A", question="May I do A?", task_id=task_id)
        second = self.plane.request_approval(
            action="Do B", question="May I do B?", task_id=task_id)
        return first, second

    def test_first_approval_keeps_task_waiting(self):
        from assistant.control.models import TaskStatus
        task = self.plane.create_task("Two approvals")
        first, _second = self._two_pending(task.id)
        self.plane.resolve_approval(first.id, approved=True)
        self.assertEqual(TaskStatus.WAITING_APPROVAL,
                         self.plane.get_task(task.id).status)

    def test_last_approval_resumes_task(self):
        from assistant.control.models import TaskStatus
        task = self.plane.create_task("Two approvals")
        first, second = self._two_pending(task.id)
        self.plane.resolve_approval(first.id, approved=True)
        self.plane.resolve_approval(second.id, approved=True)
        self.assertEqual(TaskStatus.RUNNING,
                         self.plane.get_task(task.id).status)

    def test_decline_cancels_immediately(self):
        from assistant.control.models import TaskStatus
        task = self.plane.create_task("Two approvals")
        first, _second = self._two_pending(task.id)
        self.plane.resolve_approval(first.id, approved=False)
        self.assertEqual(TaskStatus.CANCELLED,
                         self.plane.get_task(task.id).status)


class Rel03BucketCapTests(unittest.TestCase):
    """N-REL-03: the in-memory bucket cannot grow without bound."""

    def test_identities_are_capped_with_lru_eviction(self):
        from assistant.api.auth import TokenBucket
        bucket = TokenBucket(per_minute=60)
        for i in range(2500):
            bucket.take(f"caller-{i}")
        self.assertLessEqual(len(bucket._tokens), 1000)
        # Recent callers survive; the oldest are gone.
        self.assertIn("caller-2499", bucket._tokens)
        self.assertNotIn("caller-0", bucket._tokens)

    def test_rate_limiting_still_works(self):
        from assistant.api.auth import TokenBucket
        bucket = TokenBucket(per_minute=2)
        self.assertEqual(0, bucket.take("a"))
        self.assertEqual(0, bucket.take("a"))
        self.assertGreater(bucket.take("a"), 0)


class Rel04UploadSafetyTests(unittest.TestCase):
    """N-REL-04: reserved device names refused, collisions unique."""

    def setUp(self):
        import shutil
        import tempfile
        import assistant.files as files
        self.files = files
        self.root = Path(tempfile.mkdtemp(prefix="vave-rel04-")).resolve()
        self.shared = self.root / "Documents"
        self.shared.mkdir(parents=True)
        self._real_get_setting = files.get_setting
        files.get_setting = lambda key, default=None: (
            [str(self.shared)] if key == "file_shares"
            else self._real_get_setting(key, default))
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        import shutil
        self.files.get_setting = self._real_get_setting
        shutil.rmtree(self.root, ignore_errors=True)

    def _upload(self, name, data=b"x", **kwargs):
        from io import BytesIO
        return self.files.save_upload("Documents", name, BytesIO(data),
                                      **kwargs)

    def test_reserved_device_names_refused(self):
        from assistant.files import FileAccessError
        for name in ("CON", "con.txt", "NUL", "COM1", "lpt9", "AUX",
                     "file.txt:secret"):
            with self.subTest(name=name):
                with self.assertRaises(FileAccessError):
                    self._upload(name)

    def test_drive_like_prefix_is_sanitized_away(self):
        # Path("a:b.txt").name is "b.txt": the colon never reaches disk.
        entry = self._upload("a:b.txt", b"hi")
        self.assertEqual("b.txt", entry["name"])
        self.assertTrue((self.shared / "b.txt").exists())

    def test_same_second_uploads_do_not_collide(self):
        from io import BytesIO
        first = self._upload("a.txt", b"one")
        second = self._upload("a.txt", b"two")
        self.assertNotEqual(first["name"], second["name"])
        self.assertEqual(
            {"one", "two"},
            {(self.shared / first["name"]).read_text(),
             (self.shared / second["name"]).read_text()})

    def test_plain_upload_still_works(self):
        entry = self._upload("hello.txt", b"hi")
        self.assertEqual("hello.txt", entry["name"])
        self.assertTrue((self.shared / "hello.txt").exists())


if __name__ == "__main__":
    unittest.main()
