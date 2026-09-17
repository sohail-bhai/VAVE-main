"""Unit tests for Phase 6B Command Intelligence & Personalization (Wave 3)."""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from assistant.commands import execute_command, _record_usage
from assistant.control.service import ControlPlane
from assistant.control.store import ControlStore


class CommandIntelligenceTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.mkdtemp(prefix="vave-cmd-intel-test-")
        self.db_path = Path(self.tempdir) / "control.db"
        self.store = ControlStore(self.db_path)
        self.plane = ControlPlane(store=self.store)

    def tearDown(self):
        self.store.close()
        import shutil
        shutil.rmtree(self.tempdir, ignore_errors=True)

    def test_record_command_usage_and_retrieve_frequent(self):
        # 1. Record command patterns into SQLite store
        self.store.record_command_usage("time", category="system")
        self.store.record_command_usage("time", category="system")
        self.store.record_command_usage("battery", category="system")
        self.store.record_command_usage("open_website", category="web")

        frequent = self.store.get_frequent_commands(limit=5)
        self.assertGreaterEqual(len(frequent), 3)

        # "time" had 2 calls, should be top
        self.assertEqual("time", frequent[0]["command_pattern"])
        self.assertEqual(2, frequent[0]["call_count"])
        self.assertEqual("system", frequent[0]["category"])

    def test_execute_command_records_usage(self):
        with mock.patch("assistant.control.service.get_control_plane", return_value=self.plane):
            with mock.patch("assistant.system_tasks.tell_time"):
                execute_command("what time is it")

            with mock.patch("assistant.system_tasks.tell_battery"):
                execute_command("battery")

        frequent = self.store.get_frequent_commands(limit=10)
        patterns = [f["command_pattern"] for f in frequent]
        self.assertIn("time", patterns)
        self.assertIn("battery", patterns)


if __name__ == "__main__":
    unittest.main()
