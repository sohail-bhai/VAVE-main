"""Regression tests for report.md / error_report.md — Performance domain."""

import concurrent.futures
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from assistant.control.models import EventType


class Perf01TelegramForwardTests(unittest.TestCase):
    """PERF-01: forwarding a reply to Telegram must not stall speak()."""

    def setUp(self):
        from assistant import speech
        self.speech = speech
        self._was_enabled = speech._speech_enabled
        speech.set_speech_enabled(False)
        self.addCleanup(speech.set_speech_enabled, self._was_enabled)

    def _settings(self):
        return mock.patch(
            "assistant.speech.get_setting",
            side_effect=lambda key, default="": {
                "telegram_bot_token": "T",
                "telegram_chat_id": "C"}.get(key, default))

    def test_speak_returns_while_send_is_still_blocked(self):
        from assistant import call_context
        started = threading.Event()
        release = threading.Event()
        finished = threading.Event()

        def slow_send(token, chat_id, text):
            started.set()
            try:
                self.assertTrue(release.wait(timeout=10))
            finally:
                finished.set()

        token = call_context.set_origin("telegram")
        try:
            with mock.patch("assistant.telegram_sync.send_telegram_message",
                            side_effect=slow_send), self._settings():
                began = time.monotonic()
                self.speech.speak("Done")
                elapsed = time.monotonic() - began
            self.assertLess(elapsed, 3.0)
            self.assertTrue(started.wait(timeout=10))
            release.set()
            self.assertTrue(finished.wait(timeout=10))
        finally:
            call_context.reset_origin(token)

    def test_non_telegram_origin_sends_nothing(self):
        from assistant import call_context
        token = call_context.set_origin("voice")
        try:
            with mock.patch("assistant.telegram_sync.send_telegram_message") as mock_send, \
                    self._settings():
                self.speech.speak("Done")
                time.sleep(0.3)
            mock_send.assert_not_called()
        finally:
            call_context.reset_origin(token)


class Perf03SearchPruningTests(unittest.TestCase):
    """PERF-03: dependency folders are pruned from phone file search."""

    def setUp(self):
        import shutil
        import tempfile
        import assistant.files as files
        self.files = files
        self.root = Path(tempfile.mkdtemp(prefix="vave-perf03-")).resolve()
        self.shared = self.root / "Documents"
        (self.shared / "node_modules").mkdir(parents=True)
        (self.shared / "node_modules" / "big.txt").write_text("dep")
        (self.shared / ".git").mkdir(parents=True)
        (self.shared / ".git" / "objects").write_text("git")
        (self.shared / "venv").mkdir(parents=True)
        (self.shared / "venv" / "env.txt").write_text("env")
        (self.shared / "wanted.txt").write_text("mine")
        self._real_get_setting = files.get_setting
        files.get_setting = lambda key, default=None: (
            [str(self.shared)] if key == "file_shares"
            else self._real_get_setting(key, default))
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        import shutil
        self.files.get_setting = self._real_get_setting
        shutil.rmtree(self.root, ignore_errors=True)

    def test_tooling_folders_are_never_entered(self):
        self.assertEqual([], self.files.search("big"))
        self.assertEqual([], self.files.search("objects"))
        self.assertEqual([], self.files.search("env.txt"))

    def test_plain_files_still_found(self):
        hits = self.files.search("wanted")
        self.assertEqual(1, len(hits))
        self.assertTrue(hits[0]["path"].endswith("wanted.txt"))


class Perf04ToolBudgetTests(unittest.TestCase):
    """PERF-04: per-turn tool payload stays capped, never the full registry."""

    def test_payload_capped_for_representative_requests(self):
        import json
        from assistant.ai_brain import MAX_TOOLS_PER_CALL, select_tools
        self.assertLessEqual(MAX_TOOLS_PER_CALL, 16)
        for request in ("open netflix and open sohail profile",
                        "what time is it",
                        "send an email to alex saying hello",
                        "take a screenshot and tell me the battery level",
                        "", "asdf qwerty"):
            with self.subTest(request=request):
                tools = select_tools(request)
                self.assertLessEqual(len(tools), MAX_TOOLS_PER_CALL)
                self.assertLess(len(json.dumps(tools)) // 4, 2500)

    def test_full_catalogue_is_never_sent(self):
        import json
        from assistant.ai_brain import LLM_TOOLS, select_tools
        catalogue_size = len(json.dumps(LLM_TOOLS))
        for request in ("open netflix and open sohail profile", ""):
            tools = select_tools(request)
            self.assertLess(len(json.dumps(tools)), catalogue_size)


class NotifierAsyncDeliveryTests(unittest.TestCase):
    """N-PERF-01: slow channels never stall the thread recording events."""

    def setUp(self):
        import shutil
        import tempfile
        from assistant.control.notifier import Notifier
        from assistant.control.service import ControlPlane
        from assistant.control.store import ControlStore
        self.tempdir = tempfile.mkdtemp(prefix="vave-nperf01-")
        self.store = ControlStore(Path(self.tempdir) / "control.db")
        self.plane = ControlPlane(store=self.store)
        self.notifier = Notifier(self.plane)

    def tearDown(self):
        self.notifier.close()
        self.plane.close()
        self.store.close()
        import shutil
        shutil.rmtree(self.tempdir, ignore_errors=True)

    def test_record_returns_while_channel_blocked(self):
        started = threading.Event()
        release = threading.Event()
        delivered = threading.Event()

        class SlowChannel:
            name = "slow"

            def deliver(self, notification):
                started.set()
                release.wait(timeout=10)
                delivered.set()

        self.notifier.add_channel(SlowChannel())
        began = time.monotonic()
        self.plane.record("Something went wrong", EventType.TASK_FAILED)
        elapsed = time.monotonic() - began
        self.assertLess(elapsed, 3.0)
        self.assertTrue(started.wait(timeout=10))
        release.set()
        self.assertTrue(delivered.wait(timeout=10))

    def test_delivery_order_preserved(self):
        got = []
        done = threading.Event()

        class CollectingChannel:
            name = "collector"

            def deliver(self, notification):
                got.append(notification.event.message)
                if len(got) >= 2:
                    done.set()

        self.notifier.add_channel(CollectingChannel())
        self.plane.record("First thing", EventType.TASK_FAILED)
        self.plane.record("Second thing", EventType.TASK_FAILED)
        self.assertTrue(done.wait(timeout=10))
        self.assertEqual(["First thing", "Second thing"], got)

    def test_failing_channel_does_not_stop_others(self):
        got = []
        done = threading.Event()

        class BrokenChannel:
            name = "broken"

            def deliver(self, notification):
                raise ConnectionError("down")

        class GoodChannel:
            name = "good"

            def deliver(self, notification):
                got.append(notification.event.message)
                done.set()

        self.notifier.add_channel(BrokenChannel())
        self.notifier.add_channel(GoodChannel())
        self.plane.record("Something went wrong", EventType.TASK_FAILED)
        self.assertTrue(done.wait(timeout=10))
        self.assertEqual(["Something went wrong"], got)


class Perf03BrowserTimeoutTests(unittest.TestCase):
    """N-PERF-03: a wedged browser call fails fast, never hangs the task."""

    def test_timed_out_call_abandons_worker(self):
        from assistant.browser.session import _BrowserThread
        worker = _BrowserThread(timeout=0.2)
        gate = threading.Event()
        with self.assertRaises(concurrent.futures.TimeoutError):
            worker.call(gate.wait, 30)
        self.assertIsNone(worker._thread)
        self.assertEqual("ok", worker.call(lambda: "ok", timeout=5))

    def test_timeout_abandons_browser_state(self):
        from assistant.browser.session import BrowserSession
        session = BrowserSession()
        session._page = mock.sentinel.stale
        session._context = mock.sentinel.stale
        session._playwright = mock.sentinel.stale
        stub = mock.MagicMock()
        stub.call.side_effect = concurrent.futures.TimeoutError("slow")
        session._worker = stub
        with self.assertRaises(concurrent.futures.TimeoutError):
            session.describe()
        self.assertIsNone(session._page)
        self.assertIsNone(session._context)
        self.assertIsNone(session._playwright)
        self.assertEqual([], session._elements)


if __name__ == "__main__":
    unittest.main()
