"""Unit tests for OpenCode integration."""

import unittest
from unittest import mock

from assistant.integrations.opencode import (
    find_opencode_cli,
    is_opencode_available,
    list_opencode_models,
    list_opencode_models_tool,
    run_opencode,
    run_opencode_task,
)


class TestOpenCodeIntegration(unittest.TestCase):

    @mock.patch("assistant.integrations.opencode.get_setting", return_value="")
    @mock.patch("assistant.integrations.opencode._KNOWN_PATHS", [])
    @mock.patch(
        "assistant.integrations.opencode.shutil.which",
        return_value="C:/tools/opencode.exe",
    )
    def test_find_opencode_cli(self, _mock_which, _mock_setting):
        cli = find_opencode_cli()
        self.assertEqual("C:/tools/opencode.exe", cli)
        self.assertTrue(is_opencode_available())

    @mock.patch("assistant.integrations.opencode.find_opencode_cli", return_value=None)
    def test_cli_not_found_handling(self, _mock_find):
        res = run_opencode("build an app")
        self.assertFalse(res["ok"])
        self.assertIn("not installed", res["error"])

    @mock.patch("assistant.integrations.opencode.find_opencode_cli", return_value="opencode.exe")
    @mock.patch("subprocess.run")
    def test_run_opencode_success(self, mock_run, _mock_find):
        mock_run.return_value = mock.Mock(
            returncode=0,
            stdout="Created project files successfully.",
            stderr="",
        )
        res = run_opencode("scaffold a fastapi project", directory=".", model="openai/gpt-4o")
        self.assertTrue(res["ok"])
        self.assertEqual(res["exit_code"], 0)
        self.assertIn("Created project", res["output"])

        # Tool wrapper string check
        tool_out = run_opencode_task("scaffold a fastapi project")
        self.assertIn("OpenCode completed successfully", tool_out)

    @mock.patch("assistant.integrations.opencode.find_opencode_cli", return_value="opencode.exe")
    @mock.patch("subprocess.run")
    def test_run_opencode_failure(self, mock_run, _mock_find):
        mock_run.return_value = mock.Mock(
            returncode=1,
            stdout="",
            stderr="Syntax error in prompt arguments",
        )
        res = run_opencode("broken command")
        self.assertFalse(res["ok"])
        self.assertEqual(res["exit_code"], 1)

        tool_out = run_opencode_task("broken command")
        self.assertIn("OpenCode failed", tool_out)

    @mock.patch("assistant.integrations.opencode.find_opencode_cli", return_value="opencode.exe")
    @mock.patch("subprocess.run")
    def test_list_opencode_models(self, mock_run, _mock_find):
        mock_run.return_value = mock.Mock(
            returncode=0,
            stdout="openai/gpt-4o\nagentrouter/claude-opus-5\n",
            stderr="",
        )
        models = list_opencode_models()
        self.assertIn("openai/gpt-4o", models)
        self.assertIn("agentrouter/claude-opus-5", models)

        text = list_opencode_models_tool()
        self.assertIn("openai/gpt-4o", text)


if __name__ == "__main__":
    unittest.main()
