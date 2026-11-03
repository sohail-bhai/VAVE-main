"""Unit tests for VAVE Self-Evolution and Capability Gap Discovery (Phase 3)."""

import json
import os
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from assistant.self_evolution import (
    CapabilityGap,
    approve_and_implement_gap_tool,
    approve_gap,
    generate_eod_summary,
    get_capability_gap,
    implement_all_approved_gaps,
    implement_capability,
    list_capability_gaps,
    list_capability_gaps_tool,
    record_capability_gap,
    record_capability_gap_tool,
    reject_gap,
    update_gap_status,
)
import assistant.self_evolution.gaps as gaps_mod


class TestSelfEvolution(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.orig_gaps_file = gaps_mod.GAPS_FILE
        gaps_mod.GAPS_FILE = os.path.join(self.test_dir, "capability_gaps.json")

    def tearDown(self):
        gaps_mod.GAPS_FILE = self.orig_gaps_file
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_record_and_list_gaps(self):
        gap = record_capability_gap(
            task_description="Upload PDF to Telegram channel",
            missing_capability="Telegram Document Uploader",
            reason="Current telegram_sync only handles text and screenshots",
            proposed_solution="Add send_document endpoint using python-telegram-bot",
        )
        self.assertTrue(gap.id.startswith("gap_"))
        self.assertEqual(gap.status, "pending")

        pending = list_capability_gaps(status="pending")
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0].id, gap.id)

        retrieved = get_capability_gap(gap.id)
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved.missing_capability, "Telegram Document Uploader")

    def test_gap_registry_redacts_credentials(self):
        gap = record_capability_gap(
            task_description="Call API with Bearer abcdefgh12345678",
            missing_capability="Private API client",
            reason="Token secret://private_api was unavailable",
            proposed_solution="Use scoped credential",
        )
        stored = get_capability_gap(gap.id)
        self.assertNotIn("abcdefgh12345678", stored.task_description)
        self.assertNotIn("secret://private_api", stored.reason)
        self.assertIn("***", stored.task_description)

    def test_duplicate_pending_gap_is_not_recorded_twice(self):
        kwargs = {
            "task_description": "Unsupported operation",
            "missing_capability": "Missing adapter",
            "reason": "No adapter",
            "proposed_solution": "Build adapter",
        }
        first = record_capability_gap(**kwargs)
        second = record_capability_gap(**kwargs)
        self.assertEqual(first.id, second.id)
        self.assertEqual(1, len(list_capability_gaps()))

    def test_capability_gap_intent_selects_gap_tools(self):
        from assistant.ai_brain import select_tools

        names = {
            item["function"]["name"]
            for item in select_tools("show my capability gaps")
        }
        self.assertIn("list_capability_gaps", names)
        self.assertIn("record_capability_gap", names)

    @patch("assistant.self_evolution.record_capability_gap")
    def test_runtime_step_exhaustion_records_gap(self, mock_record):
        from assistant.ai_brain import _record_runtime_capability_gap

        _record_runtime_capability_gap(
            "do unsupported thing",
            "Exceeded maximum steps",
            ["read_screen", "click_element"],
        )
        mock_record.assert_called_once()
        kwargs = mock_record.call_args.kwargs
        self.assertIn("do unsupported thing", kwargs["task_description"])
        self.assertIn("click_element", kwargs["reason"])

    def test_eod_summary_empty_and_filled(self):
        empty_msg = generate_eod_summary()
        self.assertIn("No pending capability gaps logged today", empty_msg)

        gap = record_capability_gap(
            task_description="Query PostgreSQL database",
            missing_capability="PostgreSQL Client Tool",
            reason="No psql or asyncpg tool available",
            proposed_solution="Create postgres_query tool in assistant/db.py",
        )
        filled_msg = generate_eod_summary()
        self.assertIn("PostgreSQL Client Tool", filled_msg)
        self.assertIn(gap.id, filled_msg)
        self.assertIn(f"/approve_gap {gap.id}", filled_msg)

    def test_approve_and_reject_gap(self):
        gap = record_capability_gap(
            task_description="Task A",
            missing_capability="Feature A",
            reason="Missing A",
            proposed_solution="Build A",
        )
        app_res = approve_gap(gap.id)
        self.assertTrue(app_res["ok"])
        self.assertEqual(get_capability_gap(gap.id).status, "approved")

        rej_res = reject_gap(gap.id, reason="Not necessary")
        self.assertTrue(rej_res["ok"])
        updated = get_capability_gap(gap.id)
        self.assertEqual(updated.status, "rejected")
        self.assertIn("Not necessary", updated.implementation_log)

    @patch("assistant.self_evolution.auto_implementer.ProjectManager")
    def test_pending_gap_cannot_implement_without_approval(self, mock_pm_cls):
        gap = record_capability_gap(
            task_description="Task P",
            missing_capability="Feature P",
            reason="Missing P",
            proposed_solution="Build P",
        )
        result = implement_capability(gap.id)
        self.assertFalse(result["ok"])
        self.assertIn("explicitly approved", result["error"])
        mock_pm_cls.assert_not_called()

    @patch("assistant.self_evolution.implement_capability")
    def test_tool_cannot_approve_a_pending_gap(self, mock_implement):
        gap = record_capability_gap(
            task_description="Task tool boundary",
            missing_capability="Feature tool boundary",
            reason="Missing",
            proposed_solution="Build it",
        )
        result = json.loads(approve_and_implement_gap_tool(gap.id))
        self.assertFalse(result["ok"])
        self.assertIn("explicit approval", result["error"])
        self.assertEqual("pending", get_capability_gap(gap.id).status)
        mock_implement.assert_not_called()

    @patch("assistant.self_evolution.auto_implementer.ProjectManager")
    def test_implementing_gap_cannot_start_twice(self, mock_pm_cls):
        gap = record_capability_gap(
            task_description="Task D",
            missing_capability="Feature D",
            reason="Missing D",
            proposed_solution="Build D",
        )
        approve_gap(gap.id)
        update_gap_status(
            gap.id,
            "implementing",
            expected_statuses=["approved"],
        )
        result = implement_capability(gap.id)
        self.assertFalse(result["ok"])
        self.assertIn("current status: 'implementing'", result["error"])
        mock_pm_cls.assert_not_called()

    def test_failed_gap_can_be_reapproved(self):
        gap = record_capability_gap(
            task_description="Task retry",
            missing_capability="Retry feature",
            reason="First attempt failed",
            proposed_solution="Try corrected blueprint",
        )
        update_gap_status(gap.id, "failed", expected_statuses=["pending"])
        result = approve_gap(gap.id)
        self.assertTrue(result["ok"])
        self.assertEqual("approved", get_capability_gap(gap.id).status)

    @patch("assistant.self_evolution.auto_implementer.ProjectManager")
    def test_implement_capability_success(self, mock_pm_cls):
        mock_instance = MagicMock()
        mock_instance.run_managed_project.return_value = {
            "ok": True,
            "cycles_run": 1,
            "summary": "Built Feature B successfully.",
        }
        mock_pm_cls.return_value = mock_instance

        gap = record_capability_gap(
            task_description="Task B",
            missing_capability="Feature B",
            reason="Missing B",
            proposed_solution="Build B",
        )
        approve_gap(gap.id)

        res = implement_capability(gap.id)
        self.assertTrue(res["ok"])
        self.assertEqual(res["status"], "implemented")

        updated = get_capability_gap(gap.id)
        self.assertEqual(updated.status, "implemented")
        self.assertIn("Built Feature B", updated.implementation_log)

    @patch("assistant.self_evolution.auto_implementer.ProjectManager")
    def test_implement_capability_failure(self, mock_pm_cls):
        mock_instance = MagicMock()
        mock_instance.run_managed_project.return_value = {
            "ok": False,
            "cycles_run": 3,
            "summary": "Tests failed to pass.",
        }
        mock_pm_cls.return_value = mock_instance

        gap = record_capability_gap(
            task_description="Task C",
            missing_capability="Feature C",
            reason="Missing C",
            proposed_solution="Build C",
        )
        approve_gap(gap.id)

        res = implement_capability(gap.id)
        self.assertFalse(res["ok"])
        self.assertEqual(res["status"], "failed")

        updated = get_capability_gap(gap.id)
        self.assertEqual(updated.status, "failed")

    def test_tool_helpers(self):
        raw = record_capability_gap_tool(
            task_description="Deploy to AWS",
            missing_capability="AWS Deployer",
            reason="No boto3",
            proposed_solution="Add boto3 wrapper",
        )
        data = json.loads(raw)
        self.assertEqual(data["status"], "recorded")
        gap_id = data["gap_id"]

        list_raw = list_capability_gaps_tool(status="pending")
        gaps_list = json.loads(list_raw)
        self.assertEqual(len(gaps_list), 1)
        self.assertEqual(gaps_list[0]["id"], gap_id)


if __name__ == "__main__":
    unittest.main()
