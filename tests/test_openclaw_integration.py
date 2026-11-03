"""Unit tests for OpenClaw integration."""

import unittest
from unittest import mock

from assistant.integrations.openclaw import (
    find_openclaw_cli,
    is_openclaw_available,
    run_openclaw,
    run_openclaw_command,
)


class TestOpenClawIntegration(unittest.TestCase):

    @mock.patch("assistant.integrations.openclaw.get_setting", return_value="")
    @mock.patch("assistant.integrations.openclaw._NPM_GLOBAL_PATHS", [])
    @mock.patch(
        "assistant.integrations.openclaw.shutil.which",
        return_value="C:/tools/openclaw.exe",
    )
    def test_find_openclaw_cli(self, _mock_which, _mock_setting):
        cli = find_openclaw_cli()
        self.assertEqual("C:/tools/openclaw.exe", cli)
        self.assertTrue(is_openclaw_available())

    @mock.patch("assistant.integrations.openclaw.find_openclaw_cli", return_value=None)
    def test_cli_not_found(self, _mock_find):
        res = run_openclaw("status")
        self.assertFalse(res["ok"])
        self.assertIn("not installed", res["error"])

    @mock.patch("assistant.integrations.openclaw.find_openclaw_cli", return_value="openclaw.exe")
    @mock.patch("subprocess.run")
    def test_run_openclaw_success(self, mock_run, _mock_find):
        mock_run.return_value = mock.Mock(
            returncode=0,
            stdout="MoltbotDen agent online.",
            stderr="",
        )
        res = run_openclaw("status")
        self.assertTrue(res["ok"])
        self.assertEqual(res["exit_code"], 0)
        self.assertIn("online", res["output"])

        tool_out = run_openclaw_command("status")
        self.assertIn("OpenClaw output:", tool_out)

    @mock.patch("assistant.integrations.openclaw.find_openclaw_cli", return_value="openclaw.exe")
    @mock.patch("subprocess.run")
    def test_run_openclaw_failure(self, mock_run, _mock_find):
        mock_run.return_value = mock.Mock(
            returncode=1,
            stdout="",
            stderr="Invalid API key",
        )
        res = run_openclaw("ping")
        self.assertFalse(res["ok"])
        self.assertEqual(res["exit_code"], 1)

        tool_out = run_openclaw_command("ping")
        self.assertIn("OpenClaw error", tool_out)


if __name__ == "__main__":
    unittest.main()
