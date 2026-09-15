"""Unit tests for task journal and honest failure classification."""

import unittest
from unittest import mock
from pathlib import Path

from assistant import task_journal, audit
from assistant.control.models import StepResult
from tests.support import VaveTestCase


class TaskJournalTests(VaveTestCase):
    """Tests for assistant.task_journal."""

    def setUp(self):
        super().setUp()
        self.audit_file = Path(self.temp_dir) / "audit.jsonl"
        audit.configure(self.audit_file, max_bytes=1_000_000, backup_count=3)

    def test_record_task_attempt_redacts_and_appends_record(self):
        audit_id = task_journal.record_task_attempt(
            request="Send email with password SecretPass123 to friend",
            origin="voice",
            model="qwen2.5:3b",
            tools_run=["list_windows", "open_app('notepad')", "send_email"],
            step_count=3,
            outcome="out_of_steps",
            reason="Exceeded step limit with token Bearer abcdef1234567890",
        )
        self.assertTrue(audit_id)
        self.assertTrue(self.audit_file.exists())

        # Inspect logged contents
        with open(self.audit_file, "r", encoding="utf-8") as f:
            content = f.read()

        self.assertIn('"event": "task_journal"', content)
        self.assertIn('"outcome": "out_of_steps"', content)
        self.assertNotIn("Bearer abcdef1234567890", content)
        self.assertIn("send_email", content)
        # Arguments stripped from tool list
        self.assertIn('"open_app"', content)

    def test_weekly_failure_summary_aggregates_outcomes_and_tools(self):
        # Record 2 completed and 2 failures
        task_journal.record_task_attempt(
            request="what time is it",
            outcome="completed",
            tools_run=["tell_time"],
        )
        task_journal.record_task_attempt(
            request="type report",
            outcome="out_of_steps",
            tools_run=["list_windows", "focus_window"],
        )
        task_journal.record_task_attempt(
            request="click button",
            outcome="error",
            tools_run=["get_clickable_elements", "click_element"],
            reason="element not found",
        )

        summary = task_journal.get_weekly_failure_summary(days=7)
        self.assertEqual(summary["total_tasks"], 3)
        self.assertEqual(summary["failures_by_outcome"]["out_of_steps"], 1)
        self.assertEqual(summary["failures_by_outcome"]["error"], 1)
        self.assertEqual(summary["failures_by_tool"]["focus_window"], 1)
        self.assertEqual(summary["failures_by_tool"]["click_element"], 1)

    @mock.patch("assistant.ai_brain._agent_loop")
    def test_run_task_step_marks_step_result_failed_on_out_of_steps(self, mock_loop):
        from assistant.ai_brain import run_task_step

        mock_loop.return_value = {
            "outcome": "out_of_steps",
            "output": "Completed actions up to scroll.",
            "tools": ["list_windows", "scroll"],
            "steps": 12,
            "reason": "Exceeded maximum steps (12) without finishing.",
        }

        output = run_task_step("scroll down in file explorer")
        self.assertIsInstance(output, dict)
        self.assertFalse(output["ok"])
        self.assertIn("Exceeded maximum steps", output["error"])

        # Feed to StepResult
        step_result = StepResult.of(output)
        self.assertFalse(step_result.ok)
        self.assertIn("Exceeded maximum steps", step_result.error)
