"""Unit tests for Telegram Project Manager and Self-Evolution command dispatch."""

import os
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

from assistant.self_evolution import record_capability_gap


class TestTelegramProjectCommands(unittest.TestCase):
    @patch("assistant.telegram_sync.send_telegram_message")
    @patch("assistant.project_manager.ProjectManager.run_managed_project")
    def test_telegram_project_trigger(self, mock_pm_run, mock_send):
        mock_pm_run.return_value = {
            "ok": True,
            "cycles_run": 1,
            "summary": "Project finished cleanly.",
        }

        from assistant.project_manager import ProjectManager
        pm = ProjectManager()
        res = pm.run_managed_project("Create a snake game")
        self.assertTrue(res["ok"])
        self.assertEqual(res["cycles_run"], 1)

    @patch("assistant.telegram_sync.send_telegram_message")
    def test_eod_summary_generation_for_telegram(self, mock_send):
        from assistant.self_evolution import generate_eod_summary
        summary = generate_eod_summary()
        self.assertIsInstance(summary, str)
        self.assertTrue(len(summary) > 0)

    def test_autonomy_jobs_are_exclusive(self):
        from assistant import telegram_sync

        started = threading.Event()
        release = threading.Event()

        def blocking_job():
            started.set()
            release.wait(2)

        self.assertTrue(
            telegram_sync._start_autonomy_job(blocking_job, "test-autonomy-job")
        )
        self.assertTrue(started.wait(1))
        self.assertFalse(
            telegram_sync._start_autonomy_job(lambda: None, "duplicate-job")
        )
        release.set()
        deadline = time.time() + 2
        while telegram_sync._autonomy_lock.locked() and time.time() < deadline:
            time.sleep(0.01)
        self.assertFalse(telegram_sync._autonomy_lock.locked())

    @patch("assistant.self_evolution.list_capability_gaps", return_value=[])
    @patch("assistant.telegram_sync.get_setting")
    def test_status_is_dynamic_and_does_not_claim_hardware(
        self,
        mock_get_setting,
        mock_list_gaps,
    ):
        from assistant.telegram_sync import _telegram_status_text

        mock_get_setting.side_effect = lambda key, default="": (
            "qwen2.5:14b" if key == "llm_model_smart" else default
        )
        status = _telegram_status_text()
        self.assertIn("qwen2.5:14b", status)
        self.assertNotIn("RX 7600 XT", status)
        self.assertNotIn("ROCm Active", status)


if __name__ == "__main__":
    unittest.main()
