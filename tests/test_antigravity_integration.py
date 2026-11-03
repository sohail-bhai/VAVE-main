"""Unit tests for Antigravity Agent API integration harness."""

import unittest
from unittest.mock import MagicMock, patch

from assistant.integrations.antigravity import (
    get_agentapi_path,
    is_antigravity_available,
    run_antigravity_task,
)


class TestAntigravityIntegration(unittest.TestCase):
    @patch("os.path.isfile", return_value=True)
    def test_get_agentapi_path_default(self, mock_isfile):
        path = get_agentapi_path()
        self.assertIsNotNone(path)
        self.assertIn("agentapi.bat", path)

    @patch("subprocess.run")
    @patch("assistant.integrations.antigravity.get_agentapi_path", return_value="agentapi.exe")
    def test_is_antigravity_available_true(self, mock_path, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        self.assertTrue(is_antigravity_available())

    @patch("subprocess.run")
    @patch("assistant.integrations.antigravity.get_agentapi_path", return_value="agentapi.exe")
    def test_run_antigravity_task_success(self, mock_path, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="Conversation started", stderr="")
        res = run_antigravity_task("Build feature X", model="flash")
        self.assertTrue(res["ok"])
        self.assertEqual(res["engine"], "antigravity")
        self.assertEqual(res["model"], "flash")
        self.assertIn("Conversation started", res["output"])

    @patch("assistant.integrations.antigravity.get_agentapi_path", return_value=None)
    def test_run_antigravity_task_missing_cli(self, mock_path):
        res = run_antigravity_task("Build feature")
        self.assertFalse(res["ok"])
        self.assertIn("not found", res["error"])


if __name__ == "__main__":
    unittest.main()
